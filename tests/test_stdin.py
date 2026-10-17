import asyncio
import gc
import os

import pytest
from toga_dummy.utils import EventLog

from toga_console import Console, Style
from toga_console.stdin import InputPipe


def read_available(fd):
    """Everything waiting in a pipe, without blocking."""
    os.set_blocking(fd, False)
    try:
        return os.read(fd, 1 << 20)
    except BlockingIOError:
        return b""
    finally:
        os.set_blocking(fd, True)


def test_typing_into_a_readonly_console_does_nothing(app):
    console = Console()
    console._impl.type("1 + 1\n")
    assert console._impl.input == ""
    assert console.value == ""


def test_a_line_is_echoed_on_stdin_and_shown_at_once(app):
    console = Console(readonly=False)
    console.write(">>> ")
    console.flush()
    console._impl.type("1 + ")
    assert console._impl.input == "1 + "
    assert console.value == ">>> "
    console._impl.type("1\n")
    assert console._impl.input == ""
    # The echo joins the prompt's line, and the display does not wait for the flush.
    assert console.value == ">>> 1 + 1\n"
    assert console._impl.shadow == ">>> 1 + 1\n"
    assert console._pending_flush is None


def test_a_paste_enters_every_complete_line(app):
    console = Console(readonly=False)
    console._impl.type("one\r\ntwo\rthree\nfour")
    assert console.value == "one\ntwo\nthree\n"
    assert console._impl.input == "four"


def test_the_stdin_style_applies_to_the_echo(app):
    bold = Style(bold=True)
    console = Console(readonly=False, styles={"stdin": bold})
    console.write("$ ")
    console._impl.type("x\n")
    runs = EventLog.performed_actions(console, "replace tail")[-1]["runs"]
    assert runs == [("$ ", Style()), ("x\n", bold)]


def test_stdin_receives_each_line(app):
    console = Console(readonly=False)
    fd = console.stdin
    assert console.stdin == fd  # one pipe for the console's lifetime
    console._impl.type("print('hé')\n\n")
    assert read_available(fd) == "print('hé')\n\n".encode()


def test_stdin_uses_the_console_encoding(app):
    console = Console(readonly=False, encoding="latin-1")
    fd = console.stdin
    console._impl.type("é€\n")
    assert read_available(fd) == b"\xe9?\n"


def test_lines_before_stdin_exists_are_only_echoed(app):
    console = Console(readonly=False)
    console._impl.type("early\n")
    fd = console.stdin
    console._impl.type("late\n")
    assert read_available(fd) == b"late\n"
    assert console.value == "early\nlate\n"


def test_stdin_closes_with_the_console(app):
    console = Console(readonly=False)
    fd = console.stdin
    del console
    EventLog.reset()  # the log holds the widgets it saw
    gc.collect()
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(fd)


async def test_a_full_pipe_drains_in_order():
    pipe = InputPipe()
    try:
        chunk = b"x" * 65536
        lines = [bytes([65 + i % 26]) * 1000 + b"\n" for i in range(200)]
        pipe.write(chunk)
        for line in lines:
            pipe.write(line)
        assert pipe._pending  # the pipe filled
        expected = chunk + b"".join(lines)
        received = bytearray()
        for _ in range(1000):
            received += read_available(pipe.read_fd)
            if len(received) == len(expected):
                break
            await asyncio.sleep(0.001)
        assert bytes(received) == expected
        assert not pipe._pending
        assert pipe._loop is None
    finally:
        pipe.close()


async def test_closing_while_draining_stops_the_writer():
    pipe = InputPipe()
    pipe.write(b"x" * (1 << 20))
    loop, write_fd = pipe._loop, pipe._write_fd
    assert loop is not None
    pipe.close()
    assert pipe._loop is None
    assert not pipe._pending
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(write_fd)


async def test_a_drain_that_would_block_waits(monkeypatch):
    pipe = InputPipe()
    try:
        pipe.write(b"x" * (1 << 20))
        pending = len(pipe._pending)

        def would_block(fd, data):
            raise BlockingIOError

        with monkeypatch.context() as patch:
            patch.setattr(os, "write", would_block)
            pipe._drain()
        assert len(pipe._pending) == pending
    finally:
        pipe.close()
