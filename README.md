# toga-console

A [Toga](https://toga.readthedocs.io) widget that behaves like a console. Feed it bytes or text from anywhere: a subprocess's stdout and stderr, an OS pipe, an asyncio stream, a serial link. It decodes incrementally, honors carriage returns and backspaces, strips terminal escape sequences, keeps a bounded scrollback, and follows the newest output. Native backends for macOS, iOS, and Android, plus the dummy backend for tests.

## Install

```console
pip install toga-console
```

or `pip install .` from a checkout.

## Use

```python
import asyncio
import sys

import toga

from toga_console import Console


class Example(toga.App):
    def startup(self):
        self.console = Console(flex=1)
        self.main_window = toga.MainWindow()
        self.main_window.content = self.console
        self.main_window.show()

    async def on_running(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; print('hello'); print('oops', file=sys.stderr)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.console.attach(process.stdout)
        self.console.attach(process.stderr, stream="stderr")  # red by default
        await process.wait()
```

Anything running on the event loop can also call `console.write(data, stream="stdout")` directly, with bytes or text.

## API

| Member | Meaning |
|---|---|
| `Console(id=None, style=None, *, max_lines=2000, encoding="utf-8", errors="replace", follow=True, styles=None, readonly=True, placeholder=None, on_change=None, **style_properties)` | Selectable, and monospace unless a font family is given. With `readonly=False`, the user can type after the last output; see [Input](#input). |
| `write(data, *, stream="stdout")` | Append bytes or text on a named stream. Each stream has its own decoder and escape parser; all streams share one cursor and scrollback. |
| `attach(source, *, stream="stdout")` | Read a source until it ends: an `asyncio.StreamReader`, an async iterable of bytes or text, a file-like object with `fileno()`, or a file descriptor. Returns an `Attachment`. A regular file is read to its current end; on Android, descriptors are read on a helper thread so end-of-file arrives. On macOS and iOS the descriptor is switched to non-blocking for good; the widget never closes a descriptor it was given. |
| `clear()` | Empty the scrollback and the display at once. |
| `flush()` | Apply pending output to the display now instead of after the 50 ms interval. |
| `value` | The scrollback text. Assigning clears, then writes the text on `stdout`. |
| `max_lines` | Committed lines kept; assigning trims at once. |
| `follow` | Scroll to the newest output after each update. |
| `readonly` | Whether typing is refused. |
| `stdin` | A file descriptor: the read end of a pipe that receives each line typed into the console. Created on first use. |
| `styles` | A dict from stream name to `Style`; by default `stderr` is red. A change applies to text written afterwards. |
| `on_change` | Fires once per display update that changed what is shown; typing does not fire it. Use it to show a "new output" hint while the console is hidden. |
| `Attachment` | `cancel()` stops the source; `await wait()` returns at its end or after cancel and raises the error the source raised; `done`, `error`, `stream`. |
| `Style(color=None, bold=False, italic=False)` | `color` is any Toga colour value; `None` is the widget's own text colour. |

## Behaviour

| Input | Effect |
|---|---|
| `\n` | commits the line |
| `\r` | moves to the start of the line; later text overwrites, so progress lines update in place |
| `\b` | moves back one column |
| `\t` | kept |
| CSI, OSC, and other escape sequences | consumed; rendering SGR colours is a planned extension |
| other control characters | dropped |

A line reaching 4096 characters is split, a sequence of 256 characters without its terminator is abandoned, and the oldest lines drop past `max_lines`. Long lines wrap at the view's width. Each stream's decoder and escape parser keep their state across writes, so a character or sequence may span two chunks. When an attachment ends and no other live attachment shares its stream, that stream's decoder and parser are reset, so a source that dies mid-sequence cannot swallow the next source's text.

## Input

With `readonly=False`, the console accepts typing after the last output, the way a terminal does: Backspace stops at the output, and a key pressed with the caret in the output types at the end of the input. Output that arrives while you type goes in before your input, which stays where it is. Return enters the line: it is echoed into the scrollback on stream `"stdin"` (unstyled unless `styles` says otherwise) and written to `console.stdin`, encoded with the console's encoding. A paste of several lines enters each complete line.

`console.stdin` is an ordinary pipe descriptor, so anything that reads one can consume the input: a subprocess, a thread blocked in `readline`, or the event loop. Lines typed before anything reads them wait in the pipe, and lines typed before `stdin` is first used are only echoed. The console owns the pipe and closes it when the console is garbage collected; do not close it yourself.

```python
console = Console(readonly=False)
process = await asyncio.create_subprocess_exec(
    sys.executable,
    "-i",
    "-u",
    stdin=console.stdin,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
console.attach(process.stdout)
console.attach(process.stderr, stream="stderr")
```

There is no end-of-input key yet: a pipe cannot signal end-of-file without closing for good. Typing is supported on macOS; on iOS and Android the console stays read-only for now. The demo app's REPL button runs a Python REPL inside the app, on a thread that reads `console.stdin`; see `examples/console_demo/src/console_demo/repl.py`.

## Threads

`write` and every other method run on the event loop thread. From another thread, use `loop.call_soon_threadsafe(console.write, data)`. Sources given to `attach` are delivered to `write` on the loop; on Android a helper thread does the reading.

## Developing

```console
uv venv
uv pip install -e . --group dev
.venv/bin/tox -m test
```

The test suite runs against Toga's dummy backend with warnings as errors, and requires 100% coverage of everything except the native backends. `.venv/bin/pre-commit install` runs the same lint and format checks as CI before each commit.

The native backends are checked with the Briefcase app in `examples/console_demo`, which has a self-test that compares the native view's text with the model's, and on macOS types into the native view and checks the in-app REPL's answer:

```console
cd examples/console_demo
briefcase dev            # macOS
briefcase run iOS        # iOS simulator
```

On macOS and iOS the demo installs the widget straight from this repository; add `-r` after changing the widget source so Briefcase reinstalls it.

```console
uv build --wheel
cd examples/console_demo
briefcase run android -r
```

Every user-visible change needs a fragment in `changes/`, named `<issue>.<kind>.md` where kind is `feature`, `bugfix`, `removal`, `doc`, or `misc`. Release notes are assembled with `towncrier build`.

## Community

`toga-console` is part of the [BeeWare suite](https://beeware.org). You can talk to the community through:

- [@beeware@fosstodon.org on Mastodon](https://fosstodon.org/@beeware)
- [Discord](https://beeware.org/bee/chat/)

We foster a welcoming and respectful community as described in our [BeeWare Community Code of Conduct](https://beeware.org/community/behavior/).

## Contributing

If you experience problems with the `Console` widget, [log them on GitHub](https://github.com/beeware/toga-console/issues). If you want to contribute code, please [fork the code](https://github.com/beeware/toga-console) and [submit a pull request](https://github.com/beeware/toga-console/pulls).
