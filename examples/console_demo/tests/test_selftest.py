import os
import subprocess
import sys

import pytest
from toga.platform import current_platform

SELFTEST_TIMEOUT = 120
"""Seconds the demo gets to start, run its self-test, and exit."""


@pytest.mark.skipif(
    current_platform in {"iOS", "android"},
    reason="the phones cannot start the demo as a child process",
)
def test_selftest():
    # The demo runs in a child process because it needs the main thread and the
    # event loop for as long as it runs, and its exit would end the test run.
    # CONSOLE_DEMO_EXIT makes it exit once the self-test has printed its verdict.
    result = subprocess.run(
        [sys.executable, "-m", os.environ["CONSOLE_DEMO_MODULE"]],
        # The verdict quotes the console's text, emoji included, so the demo must
        # print UTF-8 even where its default is the ANSI code page, as on Windows.
        env={**os.environ, "CONSOLE_DEMO_EXIT": "1", "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        timeout=SELFTEST_TIMEOUT,
        check=False,
    )
    assert "SELFTEST OK" in result.stdout, result.stdout
