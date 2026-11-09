import asyncio
import errno
import os
import sys
import threading

import pytest
from toga_dummy.utils import EventLog

from toga_console import Attachment, Console, Style, sources
from toga_console.sources import start_pump

WINDOWS = sys.platform == "win32"


async def until(predicate, timeout=2.0):  # noqa: ASYNC109
    """Wait for ``predicate()`` to become true; fail loudly if it does not."""
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


def use_pump(request, pump):
    if WINDOWS and pump != "windows":
        # The proactor loop has no add_reader, and Windows has no select.poll.
        pytest.skip("Windows reads descriptors only with its own reader")
    if pump != "loop":
        request.getfixturevalue(f"fake_{pump}")
    return pump


@pytest.fixture(params=["loop", "android", "windows"])
def pump(request):
    """Run a descriptor test through the loop's reader, then each reader thread."""
    return use_pump(request, request.param)


@pytest.fixture(params=["android", "windows"])
def thread_pump(request):
    """Run a descriptor test through Android's reader thread, then Windows'."""
    return use_pump(request, request.param)


async def test_stream_reader_source(app):
    console = Console()
    reader = asyncio.StreamReader()
    attachment = console.attach(reader, stream="stderr")
    assert isinstance(attachment, Attachment)
    assert attachment.stream == "stderr"
    assert not attachment.done

    reader.feed_data(b"one\ntw")
    await until(lambda: console.value == "one\ntw")
    reader.feed_data(b"o\n")
    reader.feed_eof()
    await attachment.wait()
    console.flush()
    assert console.value == "one\ntwo\n"
    assert attachment.done
    assert attachment.error is None


async def test_async_iterable_source(app):
    async def chunks():
        yield b"a"
        yield "b"
        yield b"c\n"

    console = Console()
    attachment = console.attach(chunks())
    await attachment.wait()
    console.flush()
    assert console.value == "abc\n"


async def test_file_descriptor_source(app, pump):
    read_end, write_end = os.pipe()
    console = Console()
    attachment = console.attach(read_end)
    os.write(write_end, b"piped\n")
    await until(lambda: console.value == "piped\n")
    os.close(write_end)
    await attachment.wait()
    os.close(read_end)
    assert attachment.done
    assert attachment.error is None


async def test_file_object_source(app, pump):
    read_end, write_end = os.pipe()
    console = Console()
    with os.fdopen(read_end, "rb", buffering=0) as reader:
        attachment = console.attach(reader)
        os.write(write_end, b"x")
        os.close(write_end)
        await attachment.wait()
    console.flush()
    assert console.value == "x"


async def test_regular_file_source_ends_at_the_current_end(app, tmp_path):
    # A selector cannot see the end of a regular file, so one is read in a task.
    log = tmp_path / "log.txt"
    log.write_bytes(b"line 1\nline 2\n")
    console = Console()
    with log.open("rb", buffering=0) as reader:
        attachment = console.attach(reader)
        await asyncio.wait_for(attachment.wait(), 2)
    console.flush()
    assert console.value == "line 1\nline 2\n"
    assert attachment.done
    assert attachment.error is None

    # An empty file ends at once and adds nothing.
    shown = console.value
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with empty.open("rb", buffering=0) as reader:
        attachment = console.attach(reader)
        await asyncio.wait_for(attachment.wait(), 2)
    console.flush()
    assert console.value == shown
    assert attachment.done
    assert attachment.error is None


async def test_a_real_subprocess_on_both_streams(app):
    script = (
        "import sys, time\n"
        "print('out'); sys.stdout.flush(); time.sleep(0.1)\n"
        "print('err', file=sys.stderr); sys.stderr.flush(); time.sleep(0.1)\n"
        "sys.stdout.write('tail')\n"
    )
    console = Console()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out = console.attach(process.stdout)
    err = console.attach(process.stderr, stream="stderr")
    await asyncio.gather(out.wait(), err.wait(), process.wait())
    console.flush()
    assert console.value == "out\nerr\ntail"  # the partial last line shows
    assert console._impl.shadow == console.value
    runs = [
        run
        for call in EventLog.performed_actions(console, "replace tail")
        for run in call["runs"]
    ]
    assert ("err\n", Style(color="red")) in runs


async def test_cancel_stops_the_pump(app):
    console = Console()
    reader = asyncio.StreamReader()
    attachment = console.attach(reader)
    attachment.cancel()
    await attachment.wait()
    reader.feed_data(b"late")
    await asyncio.sleep(0.05)
    console.flush()
    assert console.value == ""
    assert attachment.done
    assert attachment.error is None
    attachment.cancel()  # idempotent


async def test_cancel_removes_a_file_descriptor_reader(app, pump):
    read_end, write_end = os.pipe()
    console = Console()
    attachment = console.attach(read_end)
    attachment.cancel()
    await attachment.wait()
    os.write(write_end, b"late")
    await asyncio.sleep(0.05)
    console.flush()
    assert console.value == ""
    os.close(write_end)
    os.close(read_end)


async def test_source_errors_are_kept_on_the_attachment(app):
    async def broken():
        yield b"before"
        raise OSError("gone")

    console = Console()
    attachment = console.attach(broken())
    with pytest.raises(OSError, match="gone"):
        await attachment.wait()
    console.flush()
    assert console.value == "before"
    assert isinstance(attachment.error, OSError)
    assert attachment.done


async def test_a_failing_sink_ends_a_descriptor_attachment(app, pump):
    console = Console(errors="strict")
    read_end, write_end = os.pipe()
    attachment = console.attach(read_end)
    os.write(write_end, b"\xff")
    with pytest.raises(UnicodeDecodeError):
        await asyncio.wait_for(attachment.wait(), 2)
    if not WINDOWS:  # the proactor loop has no readers to remove
        assert asyncio.get_running_loop().remove_reader(read_end) is False
    os.close(write_end)
    os.close(read_end)


async def test_a_descriptor_read_that_would_block_is_retried(app, pump, monkeypatch):
    # A wake-up can find the descriptor already drained; the pump waits for the next.
    real_read = os.read
    read_end, write_end = os.pipe()
    blocked = []

    def read(fd, length):
        if fd == read_end and not blocked:
            blocked.append(fd)
            raise BlockingIOError
        return real_read(fd, length)

    monkeypatch.setattr(os, "read", read)
    console = Console()
    attachment = console.attach(read_end)
    os.write(write_end, b"hello")
    await until(lambda: console.value == "hello")
    assert blocked == [read_end]
    attachment.cancel()
    await attachment.wait()
    os.close(write_end)
    os.close(read_end)


async def test_a_descriptor_read_error_is_kept_on_the_attachment(
    app, pump, monkeypatch
):
    real_read = os.read
    read_end, write_end = os.pipe()

    def read(fd, length):
        if fd == read_end:
            raise OSError(errno.EIO, "I/O error")
        return real_read(fd, length)

    monkeypatch.setattr(os, "read", read)
    console = Console()
    attachment = console.attach(read_end)
    os.write(write_end, b"x")
    with pytest.raises(OSError, match="I/O error"):
        await asyncio.wait_for(attachment.wait(), 2)
    assert attachment.error.errno == errno.EIO
    os.close(write_end)
    os.close(read_end)


async def test_a_thread_pump_drops_a_chunk_queued_after_it_ended(
    app, thread_pump, monkeypatch
):
    loop = asyncio.get_running_loop()
    real_read = os.read
    real_call_soon_threadsafe = loop.call_soon_threadsafe
    read_end, write_end = os.pipe()
    queued = threading.Semaphore(0)

    def read(fd, length):
        # One byte at a time, so the two bytes arrive as two chunks.
        return real_read(fd, 1 if fd == read_end else length)

    def call_soon_threadsafe(callback, *args):
        handle = real_call_soon_threadsafe(callback, *args)
        if callback.__name__ == "deliver":
            queued.release()
        return handle

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(loop, "call_soon_threadsafe", call_soon_threadsafe)
    console = Console(errors="strict")
    os.write(write_end, b"\xffa")
    attachment = console.attach(read_end)
    # Hold the loop until both chunks are queued, so the undecodable first chunk
    # ends the attachment before the second is delivered.
    for _ in range(2):
        assert queued.acquire(timeout=2)
    with pytest.raises(UnicodeDecodeError):
        await asyncio.wait_for(attachment.wait(), 2)
    await asyncio.sleep(0)
    console.flush()
    assert console.value == ""
    os.close(write_end)
    os.close(read_end)


async def test_a_pump_needs_no_end_callback():
    async def chunks():
        yield b"x"

    received = []
    attachment = start_pump(chunks(), received.append, "stdout")
    await attachment.wait()
    assert received == [b"x"]


async def test_cancelling_a_thread_pump_leaves_the_next_chunk_for_the_owner(
    app, thread_pump, bytes_waiting
):
    read_end, write_end = os.pipe()
    name = f"toga-console reader fd {read_end}"

    def readers_alive():
        return [thread.name for thread in threading.enumerate()].count(name)

    console = Console()
    attachment = console.attach(read_end)
    assert readers_alive() == 1
    os.write(write_end, b"first")
    await until(lambda: console.value == "first")
    attachment.cancel()
    await attachment.wait()
    os.write(write_end, b"late")
    await asyncio.sleep(0.1)  # time for a reader that was not woken to take it
    assert bytes_waiting(read_end) == 4, "the cancelled reader took the next chunk"
    assert os.read(read_end, 10) == b"late"
    await until(lambda: not readers_alive())
    os.close(write_end)
    os.close(read_end)


async def test_the_windows_reader_keeps_a_peek_error_on_the_attachment(
    app, fake_windows, monkeypatch
):
    def peek_pipe(fd):
        raise OSError(errno.EBADF, "The handle is invalid")

    monkeypatch.setattr(sources, "_peek_pipe", peek_pipe)
    read_end, write_end = os.pipe()
    console = Console()
    attachment = console.attach(read_end)
    with pytest.raises(OSError, match="handle is invalid"):
        await asyncio.wait_for(attachment.wait(), 2)
    os.close(write_end)
    os.close(read_end)


async def test_windows_attaches_only_pipes_and_regular_files(app, fake_windows):
    # A character device is neither; Windows has no reader for it.
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        with pytest.raises(TypeError, match="pipe or a regular file"):
            Console().attach(fd)
    finally:
        os.close(fd)


async def test_clear_during_streaming_keeps_later_output(app):
    console = Console()
    reader = asyncio.StreamReader()
    attachment = console.attach(reader)
    reader.feed_data(b"old\n")
    await until(lambda: console.value == "old\n")
    console.flush()
    console.clear()
    reader.feed_data(b"new\n")
    reader.feed_eof()
    await attachment.wait()
    console.flush()
    assert console.value == "new\n"
    assert console._impl.shadow == "new\n"


@pytest.mark.parametrize("end", ["eof", "cancel", "error"])
async def test_a_source_ending_mid_sequence_does_not_poison_the_next(app, end):
    console = Console()
    reader = asyncio.StreamReader()
    attachment = console.attach(reader)
    reader.feed_data(b"a\x1b]0;title")  # the source ends inside an OSC
    await until(lambda: console.value == "a")
    if end == "eof":
        reader.feed_eof()
        await attachment.wait()
    elif end == "cancel":
        attachment.cancel()
        await attachment.wait()
    else:
        reader.set_exception(OSError("gone"))
        with pytest.raises(OSError, match="gone"):
            await attachment.wait()

    reader = asyncio.StreamReader()
    attachment = console.attach(reader)
    reader.feed_data(b"hello\n")
    reader.feed_eof()
    await attachment.wait()
    console.flush()
    assert console.value == "ahello\n"


async def test_stream_state_is_kept_while_another_attachment_shares_the_stream(app):
    console = Console()
    first, second = asyncio.StreamReader(), asyncio.StreamReader()
    first_attachment = console.attach(first)
    second_attachment = console.attach(second)
    snake = "🐍".encode()
    first.feed_data(b"a" + snake[:2])
    await until(lambda: console.value == "a")  # half the snake waits in the decoder
    second.feed_eof()
    await second_attachment.wait()
    first.feed_data(snake[2:] + b"\n")
    first.feed_eof()
    await first_attachment.wait()
    console.flush()
    assert console.value == "a🐍\n"


async def test_attach_rejects_other_sources(app):
    # Async so a loop is running: the type check is what fails, not the loop check.
    with pytest.raises(TypeError, match="StreamReader"):
        Console().attach("not a source")


def test_attach_needs_a_running_loop(app):
    async def chunks():
        yield b"x"

    with pytest.raises(RuntimeError):
        Console().attach(chunks())
