"""A Python REPL that runs inside the app, reading the lines typed into a console.

The REPL runs on its own thread, so code that blocks does not freeze the window. It
reads each line from ``Console.stdin`` like any other reader of a pipe, and sends
its prompts and output to the console through the event loop.
"""

import asyncio
import code
import io
import sys
import threading
from functools import partial


class ThreadRouted:
    """A stand-in for ``sys.stdout`` or ``sys.stdin`` that serves one thread itself.

    The REPL thread's ``print`` and ``input`` reach the console; every other thread
    keeps the stream it had.
    """

    def __init__(self, original, thread, *, write=None, readline=None):
        self._original = original
        self._thread = thread
        self._write = write
        self._readline = readline

    def _ours(self):
        return threading.current_thread() is self._thread

    def write(self, text):
        if self._ours():
            self._write(text)
            return len(text)
        return self._original.write(text)

    def readline(self, size=-1):
        if self._ours():
            return self._readline()
        return self._original.readline(size)

    def fileno(self):
        # No descriptor, so input() asks readline() rather than the terminal.
        if self._ours():
            raise io.UnsupportedOperation("fileno")
        return self._original.fileno()

    def isatty(self):
        return False if self._ours() else self._original.isatty()

    def flush(self):
        if not self._ours():
            self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


class Exit:
    """``exit()`` and ``quit()`` for the REPL: leave it, not the app."""

    def __repr__(self):
        return "Use exit() to leave the REPL"

    def __call__(self, code=None):
        raise SystemExit(code)


class ConsoleRepl(code.InteractiveConsole):
    def __init__(self, console, namespace):
        namespace = {**namespace, "exit": Exit(), "quit": Exit()}
        super().__init__(namespace)
        self.console = console
        self.loop = asyncio.get_running_loop()
        # One reader for the REPL's life; the console keeps the descriptor.
        self.stdin = open(console.stdin, encoding="utf-8", closefd=False)  # noqa: SIM115
        self.done = self.loop.create_future()

    def start(self):
        threading.Thread(target=self._run, name="console REPL", daemon=True).start()
        return self.done

    def output(self, text, stream="stdout"):
        self.loop.call_soon_threadsafe(partial(self.console.write, text, stream=stream))

    def raw_input(self, prompt=""):
        self.output(prompt)
        line = self.stdin.readline()
        if not line:
            raise EOFError
        return line.removesuffix("\n")

    def write(self, data):
        # Tracebacks and syntax errors.
        self.output(data, "stderr")

    def _run(self):
        thread = threading.current_thread()
        stdout, stdin = sys.stdout, sys.stdin
        sys.stdout = ThreadRouted(stdout, thread, write=self.output)
        sys.stdin = ThreadRouted(stdin, thread, readline=self.stdin.readline)
        try:
            # The message is written here: interact() writes its own only on some
            # Python versions when exit() raises SystemExit.
            self.interact(
                banner=f"Python {sys.version.split()[0]} in the app. exit() leaves.",
                exitmsg="",
            )
        except SystemExit:
            pass
        finally:
            self.write("REPL ended.\n")
            sys.stdout, sys.stdin = stdout, stdin
            self.stdin.close()
            self.loop.call_soon_threadsafe(self.done.set_result, None)
