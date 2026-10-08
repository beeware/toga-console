"""The pumps behind ``Console.attach``: one task or reader hook per source."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

READ_CHUNK = 4096
"""Bytes read from a source per call."""

Sink = Callable[[bytes | str], None]


class Attachment:
    """A running source. ``cancel`` stops it; ``wait`` returns when it is over."""

    def __init__(self, stream: str):
        self.stream = stream
        self.error: BaseException | None = None
        self._done = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._cleanup: Callable[[], None] | None = None

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


def start_pump(source, sink: Sink, stream: str) -> Attachment:
    """Start reading ``source`` into ``sink`` on the running loop.

    ``source`` is an :class:`asyncio.StreamReader`, an async iterable of bytes or
    text, an object with ``fileno()``, or a file descriptor. Anything else raises
    ``TypeError``. Needs a running event loop.
    """
    loop = asyncio.get_running_loop()
    attachment = Attachment(stream)
    if isinstance(source, asyncio.StreamReader):
        pump = _pump_reader
    elif hasattr(source, "__aiter__"):
        pump = _pump_async_iterable
    elif isinstance(source, int) or hasattr(source, "fileno"):
        fd = source if isinstance(source, int) else source.fileno()
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
    except Exception as exc:
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


def _pump_fd(loop, fd: int, sink: Sink, attachment: Attachment) -> None:
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
        sink(chunk)

    attachment._cleanup = lambda: loop.remove_reader(fd)
    loop.add_reader(fd, on_readable)
