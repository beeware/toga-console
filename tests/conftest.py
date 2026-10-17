import fcntl
import os
import select
import struct
import sys
import termios

import pytest
import toga
from toga_dummy.utils import EventLog

from toga_console import sources


def pytest_configure(config):
    # Run against the dummy backend even when a real backend is installed in the venv
    # (for example after running the demo app with Briefcase).
    os.environ.setdefault("TOGA_BACKEND", "toga_dummy")


@pytest.fixture(autouse=True)
def reset_event_log():
    EventLog.reset()


@pytest.fixture
def fake_android(monkeypatch):
    """Send descriptors to the Android reader thread, which runs on any POSIX host."""
    # start_pump detects Android by this function, which only Android's sys has.
    monkeypatch.setattr(sys, "getandroidapilevel", lambda: 34, raising=False)


@pytest.fixture
def fake_windows(monkeypatch):
    """Send descriptors to the Windows pipe reader, peeking with a POSIX stand-in."""

    def waiting(fd):
        return struct.unpack("i", fcntl.ioctl(fd, termios.FIONREAD, b"\0" * 4))[0]

    def peek_pipe(fd):
        # As PeekNamedPipe: the bytes waiting, or BrokenPipeError once the writer has
        # closed and nothing is left. An empty pipe that selects readable has ended,
        # unless a write landed in between, so the count is taken again after.
        count = waiting(fd)
        if not count and select.select([fd], [], [], 0)[0]:
            count = waiting(fd)
            if not count:
                raise BrokenPipeError("the pipe has been ended")
        return count

    monkeypatch.setattr(sources, "_WINDOWS", True)
    monkeypatch.setattr(sources, "_peek_pipe", peek_pipe)


@pytest.fixture
async def app(tmp_path, monkeypatch):
    # Keep any paths the dummy backend generates inside the test's temp directory.
    monkeypatch.setenv("TOGA_DUMMY_HOME", str(tmp_path / "toga-dummy"))
    # The fixture is async so the app's event loop is the running pytest-asyncio loop.
    return toga.App(formal_name="Test App", app_id="org.beeware.toga_console.test")
