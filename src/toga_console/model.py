"""The console model: streams, escape stripping, and the scrollback.

Nothing here imports Toga. The widget feeds text through a :class:`Stream` into a
:class:`Scrollback`, then asks the scrollback what the display must do.
"""

from __future__ import annotations

import codecs
from collections import deque
from dataclasses import dataclass

from travertino.colors import Color

MAX_SEQUENCE_LENGTH = 256
"""An escape sequence this long without its terminator is abandoned and dropped."""


@dataclass(frozen=True)
class Style:
    """How a stream's text is drawn.

    ``color`` is any Toga colour value, parsed on construction (an unknown colour
    raises :class:`ValueError`), or ``None`` for the widget's own text colour.
    """

    color: Color | str | None = None
    bold: bool = False
    italic: bool = False

    def __post_init__(self) -> None:
        if self.color is not None and not isinstance(self.color, Color):
            # The dataclass is frozen, so the parsed colour goes in through object.
            object.__setattr__(self, "color", Color.parse(self.color))


PLAIN = Style()

_ESC = "\x1b"
_BEL = "\x07"
# Escape-parser states.
_GROUND, _ESCAPE, _CSI, _OSC, _OSC_ESCAPE, _INTERMEDIATE = range(6)


class Stream:
    """One named channel.

    The decoder and the escape-parser state persist across chunks, so a multibyte
    character or a control sequence split between two chunks is handled whole.
    """

    def __init__(self, name: str, encoding: str = "utf-8", errors: str = "replace"):
        self.name = name
        self._decoder = codecs.getincrementaldecoder(encoding)(errors)
        self._state = _GROUND
        self._length = 0

    def decode(self, data: bytes) -> str:
        return self._decoder.decode(data)

    def reset(self) -> None:
        """Drop a partial character or sequence left open, returning to ground."""
        self._decoder.reset()
        self._state = _GROUND
        self._length = 0

    def filter(self, text: str) -> str:
        """Return ``text`` without escape sequences, remembering one left open."""
        out: list[str] = []
        for ch in text:
            while True:
                if self._state == _GROUND:
                    if ch == _ESC:
                        self._state, self._length = _ESCAPE, 1
                    else:
                        out.append(ch)
                    break
                self._length += 1
                if self._length >= MAX_SEQUENCE_LENGTH:
                    self._state = _GROUND
                    break
                if self._state == _ESCAPE:
                    if ch == "[":
                        self._state = _CSI
                    elif ch == "]":
                        self._state = _OSC
                    elif "\x20" <= ch <= "\x2f":
                        self._state = _INTERMEDIATE
                    elif "\x30" <= ch <= "\x7e":
                        self._state = _GROUND  # a two-character sequence, complete
                    else:
                        # Not a sequence after all; the character is text.
                        self._state = _GROUND
                        continue
                elif self._state == _CSI:
                    if ch == _ESC:
                        # The sequence was cut short; the ESC opens a new one.
                        self._state, self._length = _ESCAPE, 1
                    elif "\x40" <= ch <= "\x7e":
                        self._state = _GROUND
                elif self._state == _OSC:
                    if ch == _BEL:
                        self._state = _GROUND
                    elif ch == _ESC:
                        self._state = _OSC_ESCAPE
                elif self._state == _OSC_ESCAPE:
                    if ch == "\\":
                        self._state = _GROUND
                    else:
                        # The ESC opened a new sequence instead of ending the OSC.
                        self._state, self._length = _ESCAPE, 1
                        continue
                else:  # _INTERMEDIATE
                    if ch == _ESC:
                        self._state, self._length = _ESCAPE, 1  # as in a CSI
                    elif "\x30" <= ch <= "\x7e":
                        self._state = _GROUND
                break
        return "".join(out)


DEFAULT_MAX_LINES = 2000
"""Committed lines kept; the oldest drop past this."""

MAX_LINE_LENGTH = 4096
"""A line reaching this many cells is committed as if a newline had arrived."""

Run = tuple[str, Style]
"""A stretch of text drawn in one style."""


@dataclass
class Flush:
    """What one display update must do, in order.

    ``clear`` empties the display first; otherwise ``trim`` lines are removed from the
    top. Then ``runs`` replace everything after the display's last newline; ``None``
    means the tail did not change.
    """

    clear: bool
    trim: int
    runs: list[Run] | None


def _extend(runs: list[Run], text: str, style: Style) -> None:
    """Append text to ``runs``, merging it into the last run when the style matches."""
    if runs and runs[-1][1] == style:
        runs[-1] = (runs[-1][0] + text, style)
    else:
        runs.append((text, style))


def _runs(cells: list[str], styles: list[Style]) -> list[Run]:
    runs: list[Run] = []
    for ch, style in zip(cells, styles, strict=True):
        _extend(runs, ch, style)
    return runs


class Scrollback:
    """Committed lines plus the current line with a cursor, and the flush counters.

    The display shows the committed lines as of the last flush followed by the current
    line as of that flush. ``take_flush`` turns what changed since into one
    :class:`Flush`.
    """

    def __init__(self, max_lines: int):
        self._max_lines = max_lines
        self.lines: deque[list[Run]] = deque()
        self._cells: list[str] = []
        self._styles: list[Style] = []
        self._cursor = 0
        self._displayed = 0  # committed lines the display showed after the last flush
        self._committed = 0  # lines committed since
        self._dropped = 0  # lines dropped from the top since
        self._tail_dirty = False

    @property
    def max_lines(self) -> int:
        return self._max_lines

    @max_lines.setter
    def max_lines(self, value: int) -> None:
        self._max_lines = value
        self._trim()

    @property
    def text(self) -> str:
        parts = ["".join(text for text, _ in line) + "\n" for line in self.lines]
        parts.append("".join(self._cells))
        return "".join(parts)

    def current_runs(self) -> list[Run]:
        return _runs(self._cells, self._styles)

    def put(self, text: str, style: Style) -> None:
        for ch in text:
            if ch == "\n":
                self._commit()
            elif ch == "\r":
                self._cursor = 0
            elif ch == "\b":
                self._cursor = max(0, self._cursor - 1)
            elif ch == "\t" or (ch >= " " and ch != "\x7f"):
                if "\ud800" <= ch <= "\udfff":
                    ch = "\ufffd"  # a lone surrogate: no native text view can hold one
                if self._cursor < len(self._cells):
                    self._cells[self._cursor] = ch
                    self._styles[self._cursor] = style
                else:
                    self._cells.append(ch)
                    self._styles.append(style)
                self._cursor += 1
                self._tail_dirty = True
                if len(self._cells) >= MAX_LINE_LENGTH:
                    self._commit()
            # Every other control character is dropped.

    def clear(self) -> None:
        self.lines.clear()
        self._cells, self._styles, self._cursor = [], [], 0
        self._displayed = self._committed = self._dropped = 0
        self._tail_dirty = False

    def take_flush(self) -> Flush | None:
        committed, dropped, dirty = self._committed, self._dropped, self._tail_dirty
        if not (committed or dropped or dirty):
            return None
        clear = dropped > self._displayed
        runs: list[Run] | None = None
        if committed or dirty or clear:
            runs = []
            if clear:
                new_lines = list(self.lines)
            elif committed:
                new_lines = list(self.lines)[-committed:]
            else:
                new_lines = []
            for line in new_lines:
                for text, style in line:
                    _extend(runs, text, style)
                _extend(runs, "\n", runs[-1][1] if runs else PLAIN)
            for text, style in self.current_runs():
                _extend(runs, text, style)
        self._displayed = len(self.lines)
        self._committed = self._dropped = 0
        self._tail_dirty = False
        return Flush(clear=clear, trim=0 if clear else dropped, runs=runs)

    def _commit(self) -> None:
        self.lines.append(self.current_runs())
        self._cells, self._styles, self._cursor = [], [], 0
        self._committed += 1
        self._tail_dirty = True
        self._trim()

    def _trim(self) -> None:
        while len(self.lines) > self._max_lines:
            self.lines.popleft()
            self._dropped += 1
