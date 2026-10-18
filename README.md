# toga-console

A [Toga](https://toga.readthedocs.io) widget that behaves like a console. Feed it bytes or text from anywhere: a subprocess's stdout and stderr, an OS pipe, an asyncio stream, a serial link. It decodes incrementally, honors carriage returns and backspaces, strips terminal escape sequences, keeps a bounded scrollback, and follows the newest output. Native backends for macOS, iOS, Android, Windows, Linux with GTK, and Qt, plus the dummy backend for tests.

## Install

```console
pip install toga-console
```

or `pip install .` from a checkout. The widget uses whichever Toga backend the app has installed; on Linux that is `toga-gtk` or `toga-qt`.

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
| `Console(id=None, style=None, *, max_lines=2000, encoding="utf-8", errors="replace", follow=True, styles=None, readonly=True, placeholder=None, on_change=None, on_input=None, **style_properties)` | Selectable, and monospace unless a font family is given. With `readonly=False`, the user can type after the last output; see [Input](#input). |
| `write(data, *, stream="stdout")` | Append bytes or text on a named stream. Each stream has its own decoder and escape parser; all streams share one cursor and scrollback. |
| `attach(source, *, stream="stdout")` | Read a source until it ends: an `asyncio.StreamReader`, an async iterable of bytes or text, a file-like object with `fileno()`, or a file descriptor. Returns an `Attachment`. A regular file is read to its current end. On Android, descriptors are read on a helper thread so end-of-file arrives. On Windows, a descriptor must be a pipe or a regular file, and a pipe is read on a helper thread, because Toga's Windows event loop cannot watch a descriptor; `cancel` leaves whatever arrives later in the pipe. Everywhere else the descriptor is switched to non-blocking for good. The widget never closes a descriptor it was given. |
| `clear()` | Empty the scrollback and the display at once. |
| `flush()` | Apply pending output to the display now instead of after the 50 ms interval. |
| `value` | The scrollback text. Assigning clears, then writes the text on `stdout`. |
| `max_lines` | Committed lines kept; assigning trims at once. |
| `follow` | Scroll to the newest output after each update. |
| `readonly` | Whether typing is refused. |
| `styles` | A dict from stream name to `Style`; by default `stderr` is red. A change applies to text written afterwards. |
| `on_input` | Called as `on_input(widget, data)` with the bytes the user enters: the line on Return, `b"\x03"` on Ctrl-C, `b"\x04"` on Ctrl-D. See [Input](#input). |
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

With `readonly=False`, the console accepts typing after the last output, the way a terminal does: Backspace stops at the output, and a key pressed with the caret in the output types at the end of the input. Output that arrives while you type goes in before your input, which stays where it is.

What the user enters reaches the `on_input` handler as bytes, encoded with the console's encoding:

| Key | Delivered |
|---|---|
| Return | the line and `b"\n"`, after it is echoed into the scrollback on stream `"stdin"` (unstyled unless `styles` says otherwise) |
| a paste with line breaks | all its complete lines in one call; the text after the last break stays in the input |
| Ctrl-C | `b"\x03"`, at once; the line being typed stays |
| Ctrl-D | `b"\x04"`, at once; the line being typed stays |

The console gives these bytes no meaning of its own, and does not echo the control keys: the handler decides what they do. A handler should not assume that one call is one line. Feeding a subprocess:

```python
process = await asyncio.create_subprocess_exec(
    sys.executable,
    "-i",
    "-u",
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)


def on_input(widget, data, **kwargs):
    if data == b"\x04":
        process.stdin.close()  # end of input
    elif data == b"\x03":
        process.send_signal(signal.SIGINT)
    else:
        process.stdin.write(data)


console = Console(readonly=False, on_input=on_input)
console.attach(process.stdout)
console.attach(process.stderr, stream="stderr")
```

Typing works on macOS, iOS, and Android. Ctrl-C and Ctrl-D come from a hardware keyboard; an on-screen keyboard has no Control key, so an app that needs them on a phone offers its own buttons, which can pass the same bytes to its handler. On Android, Ctrl-C in a console that takes input sends `b"\x03"` rather than copying; a read-only console copies as usual. The demo app's REPL button runs a Python REPL inside the app, on a thread fed from `on_input`, where Ctrl-C raises `KeyboardInterrupt` and Ctrl-D at the prompt ends the REPL; see `examples/console_demo/src/console_demo/repl.py`.

## Backend notes

| Backend | Native view | Notes |
|---|---|---|
| macOS | `NSTextView` | |
| iOS | `UITextView` | |
| Android | `EditText` | |
| GTK | `Gtk.TextView` | GTK 3 only. Toga's GTK 4 backend has no multiline text view yet, so the console raises an error under `TOGA_GTK=4`. |
| Qt | `QPlainTextEdit` | Qt starts a new paragraph at U+2029 and at its frame markers U+FDD0 and U+FDD1, so the view draws them as U+2028 and U+FFFD to keep one paragraph per line. `value` still holds the text as written. |
| Windows | `RichTextBox` | |

## Threads

`write` and every other method run on the event loop thread. From another thread, use `loop.call_soon_threadsafe(console.write, data)`. Sources given to `attach` are delivered to `write` on the loop; on Android, and for a pipe on Windows, a helper thread does the reading.

## Developing

```console
uv venv
uv pip install -e . --group dev
.venv/bin/tox -m test
```

The test suite runs against Toga's dummy backend with warnings as errors, and requires 100% coverage of everything except the native backends. CI runs it on Ubuntu and Windows. Code that only one of them runs is marked `# pragma: no-cover-if-is-windows` or `# pragma: no-cover-if-not-windows`, which leaves it out of the other platform's report; the combined report counts every line. `.venv/bin/pre-commit install` runs the same lint and format checks as CI before each commit.

The native backends are checked with the Briefcase project in `examples/console_demo`. Its demo app has a self-test that compares the native view's text with the model's, then types into the native view and checks the in-app REPL's answers, its Ctrl-C, and its Ctrl-D. The project has two apps: `console_demo` uses the platform's own backend, and `console_demo_qt` runs the same demo on Qt on any desktop platform:

```console
cd examples/console_demo
briefcase dev -a console_demo        # macOS, Windows, or GTK on Linux
briefcase dev -a console_demo_qt     # Qt
briefcase run iOS -a console_demo    # iOS simulator
```

On Linux, the GTK app needs [the system packages Toga's GTK backend needs](https://toga.readthedocs.io/en/stable/reference/platforms/linux/gtk.html).

Add `--test` to `briefcase dev` to run the self-test as a test suite. It starts the demo, waits for the self-test's verdict, and fails unless the demo prints `SELFTEST OK`. CI runs it for GTK and for Qt under Ubuntu, and on Windows:

```console
briefcase dev -a console_demo --test
briefcase dev -a console_demo_qt --test
```

On macOS, Linux, Windows, and iOS the demo installs the widget straight from this repository; add `-r` after changing the widget source so Briefcase reinstalls it.

```console
uv build --wheel
cd examples/console_demo
briefcase run android -a console_demo -r
```

Every user-visible change needs a fragment in `changes/`, named `<issue>.<kind>.md` where kind is `feature`, `bugfix`, `removal`, `doc`, or `misc`. Release notes are assembled with `towncrier build`.

## Community

`toga-console` is part of the [BeeWare suite](https://beeware.org). You can talk to the community through:

- [@beeware@fosstodon.org on Mastodon](https://fosstodon.org/@beeware)
- [Discord](https://beeware.org/bee/chat/)

We foster a welcoming and respectful community as described in our [BeeWare Community Code of Conduct](https://beeware.org/community/behavior/).

## Contributing

If you experience problems with the `Console` widget, [log them on GitHub](https://github.com/beeware/toga-console/issues). If you want to contribute code, please [fork the code](https://github.com/beeware/toga-console) and [submit a pull request](https://github.com/beeware/toga-console/pulls).
