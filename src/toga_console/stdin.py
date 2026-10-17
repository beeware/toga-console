"""The pipe behind ``Console.stdin``: lines typed into the console go in one end."""

from __future__ import annotations

import asyncio
import os


class InputPipe:
    """An OS pipe the console writes typed lines to.

    The read end is handed out as ``Console.stdin``, so anything that takes a
    descriptor can read the input: a subprocess, a thread blocked in ``readline``, or
    the event loop. The write end is non-blocking. When the pipe is full, the rest
    waits here and is written as the reader makes room, so typing never blocks the
    loop. Both ends stay open until :meth:`close`; like a terminal, the pipe outlives
    any one reader, and input typed ahead waits in it for the next.
    """

    def __init__(self):
        self.read_fd, self._write_fd = os.pipe()
        os.set_blocking(self._write_fd, False)
        self._pending = bytearray()
        self._loop: asyncio.AbstractEventLoop | None = None

    def write(self, data: bytes) -> None:
        if self._pending:
            self._pending += data  # still draining: keep the order
            return
        try:
            written = os.write(self._write_fd, data)
        except BlockingIOError:
            written = 0
        if written < len(data):
            self._pending += data[written:]
            self._loop = asyncio.get_running_loop()
            self._loop.add_writer(self._write_fd, self._drain)

    def _drain(self) -> None:
        try:
            written = os.write(self._write_fd, self._pending)
        except BlockingIOError:
            return
        del self._pending[:written]
        if not self._pending:
            self._stop_draining()

    def _stop_draining(self) -> None:
        if self._loop is not None:
            self._loop.remove_writer(self._write_fd)
            self._loop = None

    def close(self) -> None:
        """Close both ends; input not yet read is lost."""
        self._stop_draining()
        self._pending.clear()
        for fd in (self.read_fd, self._write_fd):
            os.close(fd)
