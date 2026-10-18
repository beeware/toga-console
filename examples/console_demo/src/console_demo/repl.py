"""A Python REPL that runs inside the app, fed by the bytes a console's on_input gets.

The REPL runs on its own thread, so code that blocks does not freeze the window.
The app passes each ``on_input`` delivery to :meth:`ConsoleRepl.feed`, and the
REPL gives the bytes the meaning a terminal would: lines are its input, Ctrl-C
(``b"\\x03"``) raises ``KeyboardInterrupt``, and Ctrl-D (``b"\\x04"``) is the end of
input, which ends the REPL at its prompt. Prompts and output go back to the console
through the event loop.
"""

import asyncio
import code
import codecs
import ctypes
import io
import sys
import threading
from collections import deque
from functools import partial

ETX = b"\x03"  # Ctrl-C
EOT = b"\x04"  # Ctrl-D
END = object()  # the end of input, queued where Ctrl-D was pressed


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
        return "Use exit() or Ctrl-D to leave the REPL"

    def __call__(self, code=None):
        raise SystemExit(code)


class ConsoleRepl(code.InteractiveConsole):
    def __init__(self, console, namespace):
        namespace = {**namespace, "exit": Exit(), "quit": Exit()}
        super().__init__(namespace)
        self.console = console
        self.loop = asyncio.get_running_loop()
        self.done = self.loop.create_future()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._partial = ""  # text after the last line break fed so far
        self._lines = deque()  # complete lines, and END for each Ctrl-D
        self._interrupted = False
        self._reading = False
        self._ready = threading.Condition()
        self._thread = threading.Thread(
            target=self._run, name="console REPL", daemon=True
        )

    def start(self):
        self._thread.start()
        return self.done

    # On the event loop

    def feed(self, data):
        """Take one ``on_input`` delivery."""
        with self._ready:
            if data == ETX:
                self._interrupt()
            elif data == EOT:
                self._lines.append(END)
            else:
                text = self._partial + self._decoder.decode(data)
                *lines, self._partial = text.split("\n")
                self._lines.extend(line + "\n" for line in lines)
            self._ready.notify()

    def _interrupt(self):
        # Called with the condition held.
        if self._reading:
            self._interrupted = True  # readline() raises it
        else:
            # Running code: raise KeyboardInterrupt in it, as SIGINT would.
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(self._thread.ident), ctypes.py_object(KeyboardInterrupt)
            )

    # On the REPL thread

    def readline(self):
        """The next line typed, ``""`` at the end of input; Ctrl-C raises."""
        with self._ready:
            self._reading = True
            try:
                while not self._lines and not self._interrupted:
                    self._ready.wait()
                if self._interrupted:
                    self._interrupted = False
                    raise KeyboardInterrupt
                line = self._lines.popleft()
            finally:
                self._reading = False
        return "" if line is END else line

    def output(self, text, stream="stdout"):
        self.loop.call_soon_threadsafe(partial(self.console.write, text, stream=stream))

    def raw_input(self, prompt=""):
        self.output(prompt)
        line = self.readline()
        if not line:
            raise EOFError
        return line.removesuffix("\n")

    def write(self, data):
        # Tracebacks, syntax errors, and the KeyboardInterrupt notice.
        self.output(data, "stderr")

    def _run(self):
        stdout, stdin = sys.stdout, sys.stdin
        sys.stdout = ThreadRouted(stdout, self._thread, write=self.output)
        sys.stdin = ThreadRouted(stdin, self._thread, readline=self.readline)
        try:
            # The message is written here: interact() writes its own only on some
            # Python versions when exit() raises SystemExit.
            self.interact(
                banner=f"Python {sys.version.split()[0]} in the app. "
                "Ctrl-D or exit() leaves.",
                exitmsg="",
            )
        except SystemExit:
            pass
        finally:
            self.write("REPL ended.\n")
            sys.stdout, sys.stdin = stdout, stdin
            self.loop.call_soon_threadsafe(self.done.set_result, None)
