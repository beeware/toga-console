"""The Console widget: a scrolling view fed by ``write``, with optional typed input."""

from __future__ import annotations

import asyncio
import codecs
import warnings
from collections.abc import Mapping
from functools import cached_property
from typing import Any, Protocol

import toga
from toga.fonts import MONOSPACE
from toga.handlers import wrapped_handler
from toga.platform import get_factory
from toga.widgets.base import StyleT
from toga.widgets.multilinetextinput import OnChangeHandler

from .model import DEFAULT_MAX_LINES, PLAIN, Scrollback, Stream, Style
from .sources import Attachment, start_pump

FLUSH_INTERVAL = 0.05
"""Seconds between a write and the display update that shows it."""

DEFAULT_STYLES: Mapping[str, Style] = {"stderr": Style(color="red")}


ETX = "\x03"
"""What Ctrl-C sends: End of Text."""
EOT = "\x04"
"""What Ctrl-D sends: End of Transmission."""


class OnInputHandler(Protocol):
    def __call__(self, widget: Console, data: bytes, **kwargs: Any) -> object:
        """A handler to invoke with the bytes the user enters into the console.

        :param widget: The console the bytes were entered into.
        :param data: The bytes: one or more lines, each ending with ``b"\\n"``, or a
            single control byte, ``b"\\x03"`` for Ctrl-C or ``b"\\x04"`` for Ctrl-D.
        :param kwargs: Ensures compatibility with arguments added in future versions.
        """


class Console(toga.MultilineTextInput):
    def __init__(
        self,
        id: str | None = None,
        style: StyleT | None = None,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        encoding: str = "utf-8",
        errors: str = "replace",
        follow: bool = True,
        styles: Mapping[str, Style] | None = None,
        readonly: bool = True,
        placeholder: str | None = None,
        on_change: OnChangeHandler | None = None,
        on_input: OnInputHandler | None = None,
        **kwargs,
    ):
        """Create a new console.

        :param id: The ID for the widget.
        :param style: A style object. If no style is provided, a default style will
            be applied to the widget.
        :param max_lines: Committed lines kept in the scrollback; the oldest drop.
        :param encoding: The encoding used to decode bytes written to any stream.
        :param errors: The decoding error policy, as for :func:`codecs.lookup_error`.
        :param follow: Keep the newest output in view after each update.
        :param styles: A mapping from stream name to :class:`Style`, merged over the
            default, which draws ``stderr`` in red.
        :param readonly: Whether the console refuses typed input. When ``False``, the
            user can type after the last output; each line entered is echoed on
            stream ``"stdin"`` and passed to ``on_input``.
        :param placeholder: The content to display when the console is empty.
        :param on_change: A handler invoked once per display update that changed
            what is shown.
        :param on_input: A handler invoked with the bytes the user enters: each line
            on Enter, and a single control byte for Ctrl-C or Ctrl-D.
        :param kwargs: Initial style properties. Unless a font family is given here or
            on ``style``, the console uses a monospace font.
        """
        if max_lines < 1:
            raise ValueError("max_lines must be at least 1")
        # Unknown names raise LookupError here, before anything is built.
        codecs.lookup(encoding)
        codecs.lookup_error(errors)
        if "font_family" not in kwargs and (
            style is None or "font_family" not in style
        ):
            kwargs["font_family"] = MONOSPACE

        # State the inherited value setter needs before super().__init__() runs.
        self._encoding = encoding
        self._errors = errors
        self._scrollback = Scrollback(max_lines)
        self._streams: dict[str, Stream] = {}
        self._live_attachments: dict[str, int] = {}  # per stream name
        self._styles: dict[str, Style] = dict(DEFAULT_STYLES)
        if styles is not None:
            self._styles.update(styles)
        self._follow = follow
        self._pending_flush: asyncio.Task | None = None

        super().__init__(
            id=id,
            style=style,
            value=None,
            readonly=readonly,
            placeholder=placeholder,
            on_change=on_change,
            **kwargs,
        )
        self.on_input = on_input

    @cached_property
    def factory(self):
        # This is an official BeeWare external widget, so its interface group uses the
        # "toga_" prefix. Toga currently warns about any "toga_" interface it does not
        # know. Until Toga provides a way to register external widgets, swallow that
        # one warning here rather than letting every app print it at startup.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Unrecognized official Toga interface",
                category=RuntimeWarning,
            )
            return get_factory("toga_console")

    def _create(self) -> Any:
        return self.factory.Console(interface=self)

    # Content

    @property
    def value(self) -> str:
        """The scrollback text.

        Assigning clears the console, then writes the text on stream ``"stdout"``.
        """
        return self._scrollback.text

    @value.setter
    def value(self, value: object) -> None:
        if self._scrollback.text or self._pending_flush is not None:
            self.clear()
        text = "" if value is None else str(value)
        if text:
            self.write(text)

    def write(self, data: bytes | str, *, stream: str = "stdout") -> None:
        """Append ``data`` to the console on the named stream.

        Bytes are decoded with the stream's own decoder, so a multibyte character may
        span two calls. Escape sequences are stripped. The display updates shortly
        afterwards; see :meth:`flush`.
        """
        channel = self._stream(stream)
        if isinstance(data, str):
            text = data
        elif isinstance(data, (bytes, bytearray, memoryview)):
            text = channel.decode(bytes(data))
        else:
            raise TypeError(f"write() takes bytes or str, not {type(data).__name__}")
        text = channel.filter(text)
        if text:
            self._scrollback.put(text, self._styles.get(stream, PLAIN))
            self._schedule_flush()

    def attach(self, source, *, stream: str = "stdout") -> Attachment:
        """Read a source into the console until it ends.

        ``source`` is an :class:`asyncio.StreamReader` (a subprocess pipe, for
        example), an async iterable of bytes or text, a file-like object with
        ``fileno()``, or a file descriptor. A regular file is read to its current
        end, not followed. Returns the :class:`Attachment`, which can cancel the
        source or be awaited for its end.
        """

        def ended() -> None:
            # Once no live source shares the stream, a character or sequence this one
            # left open must not swallow the next source's text.
            self._live_attachments[stream] -= 1
            if not self._live_attachments[stream]:
                del self._live_attachments[stream]
                if (channel := self._streams.get(stream)) is not None:
                    channel.reset()

        attachment = start_pump(
            source, lambda data: self.write(data, stream=stream), stream, on_end=ended
        )
        # Counted once start_pump returns; no attachment ends before then.
        self._live_attachments[stream] = self._live_attachments.get(stream, 0) + 1
        return attachment

    def clear(self) -> None:
        """Empty the scrollback and the display at once."""
        self._cancel_pending_flush()
        self._scrollback.clear()
        self._impl.clear()

    def flush(self) -> None:
        """Apply pending output to the display now instead of after the interval."""
        self._cancel_pending_flush()
        self._apply_flush()

    @property
    def max_lines(self) -> int:
        """Committed lines kept; assigning a smaller value trims at once."""
        return self._scrollback.max_lines

    @max_lines.setter
    def max_lines(self, value: int) -> None:
        if value < 1:
            raise ValueError("max_lines must be at least 1")
        self._scrollback.max_lines = value
        self.flush()

    @property
    def follow(self) -> bool:
        """Whether each display update scrolls to the newest output."""
        return self._follow

    @follow.setter
    def follow(self, value: object) -> None:
        self._follow = bool(value)
        if self._follow:
            self._impl.scroll_to_bottom()

    @property
    def styles(self) -> dict[str, Style]:
        """Stream name to style. A change applies to text written afterwards."""
        return self._styles

    @property
    def on_input(self) -> OnInputHandler:
        """The handler invoked with the bytes the user enters into the console.

        Typed text is delivered when Enter is pressed, as the line plus ``b"\\n"``;
        a paste delivers its complete lines at once. Ctrl-C delivers ``b"\\x03"``
        and Ctrl-D ``b"\\x04"`` at once, leaving a half-typed line in place. The
        console gives these bytes no meaning of its own: the handler decides what
        they do, such as writing them to a subprocess's stdin, closing it on
        Ctrl-D, or sending it ``SIGINT`` on Ctrl-C. Text is encoded with the
        console's encoding. A read-only console delivers nothing.
        """
        return self._on_input

    @on_input.setter
    def on_input(self, handler: OnInputHandler | None) -> None:
        self._on_input = wrapped_handler(self, handler)

    # Internals

    def _stream(self, name: str) -> Stream:
        try:
            return self._streams[name]
        except KeyError:
            channel = self._streams[name] = Stream(name, self._encoding, self._errors)
            return channel

    def _enter_input(self, text: str) -> str:
        """Take the text in the console's input area from the backend.

        The backend calls this when the input area's text would contain a line
        break: on Enter, or on a paste. The complete lines are echoed on stream
        ``"stdin"``, the display is updated at once, and then they are passed to
        ``on_input`` together. Returns the text after the last line break, which
        stays in the input area.
        """
        *lines, rest = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines:
            entered = "".join(line + "\n" for line in lines)
            self.write(entered, stream="stdin")
            self.flush()
            self.on_input(data=entered.encode(self._encoding, self._errors))
        return rest

    def _enter_control(self, char: str) -> None:
        """Pass a control key's byte to ``on_input``; the backend calls this."""
        if char not in (ETX, EOT):
            raise ValueError(f"not a console control key: {char!r}")
        self.on_input(data=char.encode("ascii"))

    def _schedule_flush(self) -> None:
        if self._pending_flush is not None:
            if not self._pending_flush.done():
                return
            self._pending_flush = None  # cancelled from outside before it could flush
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._apply_flush()  # no loop to wait on: show it now
            return
        # A task, not a bare call_later: Toga's Android loop only wakes for the former.
        self._pending_flush = loop.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(FLUSH_INTERVAL)
        self._pending_flush = None
        self._apply_flush()

    def _cancel_pending_flush(self) -> None:
        task, self._pending_flush = self._pending_flush, None
        if task is not None:
            task.cancel()

    def _apply_flush(self) -> None:
        flush = self._scrollback.take_flush()
        if flush is None:
            return
        if flush.clear:
            self._impl.clear()
        elif flush.trim:
            self._impl.trim_lines(flush.trim)
        if flush.runs is not None:
            self._impl.replace_tail(flush.runs)
        if self._follow:
            self._impl.scroll_to_bottom()
        self.on_change()
