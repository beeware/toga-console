"""The pumps behind ``Console.attach``: one task, reader hook, or thread per source."""

from __future__ import annotations

import asyncio
import contextlib
import os
import select
import stat
import sys
import threading
from collections.abc import Callable

READ_CHUNK = 4096
"""Bytes read from a source per call."""

PIPE_POLL_INTERVAL = 0.02
"""Seconds a Windows pipe reader waits before it looks at an empty pipe again."""

_WINDOWS = sys.platform == "win32"

Sink = Callable[[bytes | str], None]


class Attachment:
    """A running source. ``cancel`` stops it; ``wait`` returns when it is over."""

    def __init__(self, stream: str):
        self.stream = stream
        self.error: BaseException | None = None
        self._done = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._cleanup: Callable[[], None] | None = None
        self._on_end: Callable[[], None] | None = None

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def cancel(self) -> None:
        """Stop the source. Safe to call more than once, or after it ended."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._finish()

    async def wait(self) -> None:
        """Return at EOF or after ``cancel``; raise the error the source raised."""
        await self._done.wait()
        if self.error is not None:
            raise self.error

    def _finish(self, error: BaseException | None = None) -> None:
        if self._done.is_set():
            return
        if self._cleanup is not None:
            self._cleanup()
            self._cleanup = None
        self.error = error
        self._done.set()
        if self._on_end is not None:
            self._on_end()
            self._on_end = None


def start_pump(
    source, sink: Sink, stream: str, on_end: Callable[[], None] | None = None
) -> Attachment:
    """Start reading ``source`` into ``sink`` on the running loop.

    ``source`` is an :class:`asyncio.StreamReader`, an async iterable of bytes or
    text, an object with ``fileno()``, or a file descriptor. A regular file is read
    to its current end; it is not followed. Anything else raises ``TypeError``.
    Needs a running event loop. ``on_end`` is called once, on the loop, when the
    attachment ends for any reason, after ``done`` is set.
    """
    loop = asyncio.get_running_loop()
    attachment = Attachment(stream)
    attachment._on_end = on_end
    if isinstance(source, asyncio.StreamReader):
        pump = _pump_reader
    elif hasattr(source, "__aiter__"):
        pump = _pump_async_iterable
    elif isinstance(source, int) or hasattr(source, "fileno"):
        fd = source if isinstance(source, int) else source.fileno()
        mode = os.fstat(fd).st_mode
        if stat.S_ISREG(mode):
            # A selector cannot see the end of a regular file: kqueue stops reporting
            # it readable there, and epoll refuses it. Read it to its end in a task.
            attachment._task = loop.create_task(_run(_pump_file, fd, sink, attachment))
        elif _WINDOWS:
            if not stat.S_ISFIFO(mode):
                raise TypeError(
                    "cannot attach this descriptor: on Windows, a descriptor must be "
                    "a pipe or a regular file"
                )
            _pump_pipe_thread(loop, fd, sink, attachment)
        elif hasattr(sys, "getandroidapilevel"):  # pragma: no-cover-if-is-windows
            _pump_fd_thread(loop, fd, sink, attachment)
        else:  # pragma: no-cover-if-is-windows
            _pump_fd(loop, fd, sink, attachment)
        return attachment
    else:
        raise TypeError(
            f"cannot attach {type(source).__name__}: expected a StreamReader, "
            "an async iterable, a file-like object, or a file descriptor"
        )
    attachment._task = loop.create_task(_run(pump, source, sink, attachment))
    return attachment


async def _run(pump, source, sink: Sink, attachment: Attachment) -> None:
    # The pump is called here, inside the task: a task cancelled before its first
    # step never runs this body, and a coroutine built earlier would go unawaited.
    try:
        await pump(source, sink)
    except asyncio.CancelledError:
        attachment._finish()
        raise
    except Exception as exc:  # noqa: BLE001
        attachment._finish(exc)
    else:
        attachment._finish()


async def _pump_reader(reader: asyncio.StreamReader, sink: Sink) -> None:
    while True:
        chunk = await reader.read(READ_CHUNK)
        if not chunk:
            return
        sink(chunk)


async def _pump_async_iterable(source, sink: Sink) -> None:
    async for chunk in source:
        sink(chunk)


async def _pump_file(fd: int, sink: Sink) -> None:
    while True:
        chunk = os.read(fd, READ_CHUNK)
        if not chunk:
            return
        sink(chunk)
        await asyncio.sleep(0)  # let the loop run between the chunks of a large file


def _pump_fd(  # pragma: no-cover-if-is-windows
    loop, fd: int, sink: Sink, attachment: Attachment
) -> None:
    os.set_blocking(fd, False)

    def on_readable() -> None:
        try:
            chunk = os.read(fd, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError as exc:
            attachment._finish(exc)
            return
        if not chunk:
            attachment._finish()
            return
        try:
            sink(chunk)
        except Exception as exc:  # noqa: BLE001
            attachment._finish(exc)

    attachment._cleanup = lambda: loop.remove_reader(fd)
    loop.add_reader(fd, on_readable)


def _pump_fd_thread(  # pragma: no-cover-if-is-windows
    loop, fd: int, sink: Sink, attachment: Attachment
) -> None:
    """Read a descriptor on a daemon thread, for Android.

    Toga's Android selector passes on only read and write readiness, and Android
    reports a pipe whose writer has closed as a hang-up alone, so a reader added with
    ``add_reader`` never sees the EOF. This thread waits in ``poll()`` instead, which
    always reports a hang-up, and the read that follows returns the EOF. It also
    polls a private wake pipe. The attachment's cleanup writes a byte to the wake
    pipe and closes its write end, so ``cancel`` ends the thread without another
    read, and whatever arrives later stays in the descriptor for its owner. The
    thread closes the wake pipe's read end as it exits. The descriptor's blocking
    mode is left as it is; a read that would block is retried.
    """
    wake_r, wake_w = os.pipe()

    def deliver(chunk: bytes) -> None:
        if attachment.done:
            return
        try:
            sink(chunk)
        except Exception as exc:  # noqa: BLE001
            attachment._finish(exc)

    def wake() -> None:
        # Attachment._finish calls this once, on the loop. The thread may already
        # have closed its end, so the write can fail.
        with contextlib.suppress(OSError):
            os.write(wake_w, b"\0")
        with contextlib.suppress(OSError):
            os.close(wake_w)

    def read() -> None:
        poller = select.poll()
        poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        poller.register(wake_r, select.POLLIN)
        try:
            while True:
                if any(ready == wake_r for ready, _events in poller.poll()):
                    break
                try:
                    chunk = os.read(fd, READ_CHUNK)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    loop.call_soon_threadsafe(attachment._finish, exc)
                    break
                if not chunk:
                    loop.call_soon_threadsafe(attachment._finish)
                    break
                loop.call_soon_threadsafe(deliver, chunk)
        finally:
            os.close(wake_r)

    attachment._cleanup = wake
    threading.Thread(
        target=read, name=f"toga-console reader fd {fd}", daemon=True
    ).start()


def _pump_pipe_thread(loop, fd: int, sink: Sink, attachment: Attachment) -> None:
    """Read a pipe on a daemon thread, for Windows.

    Toga's Windows event loop is a proactor, which cannot watch a descriptor, and an
    anonymous pipe cannot be read with overlapped I/O. A blocking read on a thread
    could not be woken by ``cancel``, and would take the next chunk from the pipe's
    owner. This thread reads only what ``PeekNamedPipe`` reports waiting, so no read
    blocks, and between looks at an empty pipe it waits on a stop event. The peek
    reports a broken pipe once the writer has closed and the pipe is drained, which
    is the end. The attachment's cleanup sets the stop event, so ``cancel`` ends the
    thread without another read, and whatever arrives later stays in the pipe for its
    owner. A read that would block, because something else drained the pipe first,
    is retried.
    """
    stop = threading.Event()

    def deliver(chunk: bytes) -> None:
        if attachment.done:
            return
        try:
            sink(chunk)
        except Exception as exc:  # noqa: BLE001
            attachment._finish(exc)

    def read() -> None:
        while not stop.is_set():
            try:
                waiting = _peek_pipe(fd)
            except BrokenPipeError:
                loop.call_soon_threadsafe(attachment._finish)
                return
            except OSError as exc:
                loop.call_soon_threadsafe(attachment._finish, exc)
                return
            if not waiting:
                stop.wait(PIPE_POLL_INTERVAL)
                continue
            try:
                chunk = os.read(fd, min(waiting, READ_CHUNK))
            except BlockingIOError:
                continue
            except OSError as exc:
                loop.call_soon_threadsafe(attachment._finish, exc)
                return
            loop.call_soon_threadsafe(deliver, chunk)

    attachment._cleanup = stop.set
    threading.Thread(
        target=read, name=f"toga-console reader fd {fd}", daemon=True
    ).start()


def _peek_pipe(fd: int) -> int:  # pragma: no-cover-if-not-windows
    """Return the bytes waiting in a Windows pipe without reading them.

    Raises :class:`BrokenPipeError` once the writer has closed and the pipe is empty.
    Only Windows has these modules; elsewhere the tests run the reader with a POSIX
    stand-in.
    """
    import _winapi
    import msvcrt

    return _winapi.PeekNamedPipe(msvcrt.get_osfhandle(fd), 0)[0]
