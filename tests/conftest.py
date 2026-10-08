import os

import pytest
import toga
from toga_dummy.utils import EventLog


def pytest_configure(config):
    # Run against the dummy backend even when a real backend is installed in the venv
    # (for example after running the demo app with Briefcase).
    os.environ.setdefault("TOGA_BACKEND", "toga_dummy")


@pytest.fixture(autouse=True)
def reset_event_log():
    EventLog.reset()


@pytest.fixture
async def app(tmp_path, monkeypatch):
    # Keep any paths the dummy backend generates inside the test's temp directory.
    monkeypatch.setenv("TOGA_DUMMY_HOME", str(tmp_path / "toga-dummy"))
    # The fixture is async so the app's event loop is the running pytest-asyncio loop.
    return toga.App(formal_name="Test App", app_id="org.beeware.toga_console.test")
