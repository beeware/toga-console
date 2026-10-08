"""The console model: streams, escape stripping, and the scrollback.

Nothing here imports Toga. The widget feeds text through a :class:`Stream` into a
:class:`Scrollback`, then asks the scrollback what the display must do.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

MAX_SEQUENCE_LENGTH = 256
"""An escape sequence this long without its terminator is abandoned and dropped."""


@dataclass(frozen=True)
class Style:
    """How a stream's text is drawn.

    ``color`` is any Toga colour value, or ``None`` for the widget's own text colour.
    """

    color: str | None = None
    bold: bool = False
    italic: bool = False


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
                    if "\x40" <= ch <= "\x7e":
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
                    if "\x30" <= ch <= "\x7e":
                        self._state = _GROUND
                break
        return "".join(out)
