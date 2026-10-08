"""Console Demo: feeds the toga-console widget from a burst, a pipe, and a subprocess.

The self-test runs at startup and from its button: it writes the burst directly,
then streams the same bytes through an OS pipe, and compares the native view's
text with the model's text and with the expected text. It then trims the console
to seven lines and compares the native view's text with the model's text and
with the last seven expected lines. The verdict is printed as ``SELFTEST OK`` or
``SELFTEST FAIL`` so a log reader can check a backend without eyes on the screen.
"""

import asyncio
import os
import sys
import threading
import time

import toga
from toga.constants import COLUMN, ROW
from toga.platform import current_platform

from toga_console import Console
from toga_console.model import DEFAULT_MAX_LINES

BURST = [
    b"Hello from the burst\r\n",
    b"Progress: 0%\r",
    b"Progress: 50%\r",
    b"Progress: 100%\r\n",
    b"Backspace demo: abcX\x08Y\r\n",
    b"\x1b]0;Title bar status\x1b\\",
    b"\x1b[31mRed via CSI, stripped\x1b[0m\r\n",
    "Split emoji: 🐍".encode()[:-2],
    "🐍".encode()[-2:] + b" done\r\n",
    # A combining accent that opens a line can hide the newline before it.
    b"\xcc\x81 accent",
    b" line\r\n",
]
EXPECTED = (
    "Hello from the burst\n"
    "Progress: 100%\n"
    "Backspace demo: abcY\n"
    "Red via CSI, stripped\n"
    "Split emoji: 🐍 done\n"
    "\u0301 accent line\n"
)
# The self-test's trim keeps the last seven of its twelve lines.
TRIMMED = "\u0301 accent line\n" + EXPECTED
CHILD_SCRIPT = (
    "import sys, time\n"
    "for i in range(3):\n"
    "    print('stdout line', i); sys.stdout.flush(); time.sleep(0.2)\n"
    "print('stderr line', file=sys.stderr)\n"
)


class ConsoleDemo(toga.App):
    def startup(self):
        self.console = Console(flex=1, placeholder="Press a button.")
        self.result = toga.Label("", flex=1)
        self.follow = toga.Switch("Follow", value=True, on_change=self.set_follow)
        buttons = [
            toga.Button("Burst", on_press=self.burst),
            toga.Button("Pipe", on_press=self.pipe),
            toga.Button("Self-test", on_press=self.run_self_test),
            toga.Button("Clear", on_press=self.clear),
        ]
        if current_platform == "macOS":  # the phones cannot spawn a Python child
            buttons.insert(2, toga.Button("Subprocess", on_press=self.subprocess))

        # Two rows so the controls fit a phone's width.
        toolbar = toga.Box(
            children=[
                toga.Box(children=buttons, direction=ROW, gap=5),
                toga.Box(
                    children=[self.follow, self.result],
                    direction=ROW,
                    align_items="center",
                    gap=5,
                ),
            ],
            direction=COLUMN,
            margin=5,
            gap=5,
        )
        self.main_window = toga.MainWindow()
        self.main_window.content = toga.Box(
            children=[toolbar, self.console], direction=COLUMN
        )
        self.main_window.show()

    async def on_running(self):
        await self.self_test()
        if os.environ.get("CONSOLE_DEMO_EXIT"):
            self.request_exit()

    def burst(self, widget=None, **kwargs):
        for chunk in BURST:
            self.console.write(chunk)

    def pipe(self, widget=None, **kwargs):
        read_end, write_end = os.pipe()

        def feed():
            for chunk in BURST:
                os.write(write_end, chunk)
                # Outlast the 50 ms flush interval, so each chunk is shown on its own.
                time.sleep(0.1)
            os.close(write_end)

        attachment = self.console.attach(read_end, stream="stderr")
        threading.Thread(target=feed, daemon=True).start()
        asyncio.get_running_loop().create_task(self._close_after(attachment, read_end))

    async def _close_after(self, attachment, fd):
        try:
            await attachment.wait()
        finally:
            os.close(fd)

    async def subprocess(self, widget=None, **kwargs):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            CHILD_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.console.attach(process.stdout)
        self.console.attach(process.stderr, stream="stderr")
        await process.wait()

    def clear(self, widget=None, **kwargs):
        self.console.clear()

    def set_follow(self, widget, **kwargs):
        self.console.follow = self.follow.value

    async def run_self_test(self, widget=None, **kwargs):
        await self.self_test()

    async def self_test(self):
        self.console.clear()
        self.burst()
        await asyncio.sleep(0.2)
        self.pipe()
        for _ in range(100):
            await asyncio.sleep(0.05)
            if self.console.value.count("accent line") >= 2:
                break
        await asyncio.sleep(0.2)
        native, model = self.console._impl.get_value(), self.console.value
        ok = native == model == EXPECTED * 2
        # Twelve lines down to seven: the trim runs through the newline that precedes
        # the combining-character line.
        self.console.max_lines = 7
        await asyncio.sleep(0.2)
        trimmed_native = self.console._impl.get_value()
        trimmed_model = self.console.value
        self.console.max_lines = DEFAULT_MAX_LINES
        ok = ok and trimmed_native == trimmed_model == TRIMMED
        verdict = "OK" if ok else "FAIL"
        self.result.text = f"Self-test {verdict}"
        print(
            f"SELFTEST {verdict} native={native!r} model={model!r} "
            f"trimmed_native={trimmed_native!r} trimmed_model={trimmed_model!r}",
            flush=True,
        )


def main():
    return ConsoleDemo("Console Demo", "org.beeware.examples.console_demo")
