import asyncio
import os
import select
import sys
import threading

import pytest
from toga_dummy.utils import EventLog

from toga_console import Attachment, Console, Style


async def until(predicate, timeout=2.0):  # noqa: ASYNC109
    """Wait for ``predicate()`` to become true; fail loudly if it does not."""
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


@pytest.fixture(params=["loop", "thread"])
def pump(request):
    """Run a descriptor test through the loop's reader, then Android's reader thread."""
    if request.param == "thread":
        request.getfixturevalue("fake_android")
    return request.param


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
    assert attachment.done and attachment.error is None


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
    assert attachment.done and attachment.error is None


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
    assert attachment.done and attachment.error is None

    # An empty file ends at once and adds nothing.
    shown = console.value
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with empty.open("rb", buffering=0) as reader:
        attachment = console.attach(reader)
        await asyncio.wait_for(attachment.wait(), 2)
    console.flush()
    assert console.value == shown
    assert attachment.done and attachment.error is None


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
    assert attachment.done and attachment.error is None
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
    assert asyncio.get_running_loop().remove_reader(read_end) is False
    os.close(write_end)
    os.close(read_end)


async def test_cancelling_a_thread_pump_leaves_the_next_chunk_for_the_owner(
    app, fake_android
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
    readable, _, _ = select.select([read_end], [], [], 0)
    assert readable == [read_end], "the cancelled reader took the next chunk"
    assert os.read(read_end, 10) == b"late"
    await until(lambda: not readers_alive())
    os.close(write_end)
    os.close(read_end)


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
