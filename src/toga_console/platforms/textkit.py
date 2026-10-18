"""The console backend contract over an NSTextStorage, shared by macOS and iOS.

The native text is the output, then the input area: what the user is typing.
``output_length`` is where the input area starts, in NSString units. The output
calls edit only the text before it, and :meth:`TextKitConsole.should_change` lets
the user edit only the text after it.
"""

from contextlib import contextmanager

from rubicon.objc import NSRange, ObjCClass

from ..model import PLAIN

NSAttributedString = ObjCClass("NSAttributedString")

# NSStringCompareOptions. Literal search matches a newline even when a combining
# character follows it; the default comparison treats the pair as one character.
NSLiteralSearch = 2
NSBackwardsSearch = 4
# NSNotFound is NSIntegerMax.
NSNotFound = 0x7FFFFFFFFFFFFFFF

# The console's control keys, by the key pressed with Control.
CONTROL_KEYS = {"c": "\x03", "d": "\x04"}


def utf16_length(text):
    """The length of ``text`` as NSString counts it."""
    return len(text.encode("utf-16-le")) // 2


class TextKitConsole:
    """A mixin for a backend whose ``text_view`` is an NSTextView or a UITextView.

    The backend provides ``text_view`` and ``_attributes(style)``, and sets
    ``output_length = 0`` and ``input_readonly = True`` when it creates the view.
    ``_edited()`` runs after every change the console makes to the storage.
    """

    def _edited(self):
        pass

    def get_value(self):
        string = self.text_view.textStorage.string
        return str(string.substringToIndex(self.output_length))

    # Console backend contract

    def replace_tail(self, runs):
        # Build every piece first: an exception between beginEditing and endEditing
        # would leave the storage mid-edit.
        pieces = [
            NSAttributedString.alloc().initWithString(
                text, attributes=self._attributes(style)
            )
            for text, style in runs
        ]
        storage = self.text_view.textStorage
        end = self.output_length
        found = storage.string.rangeOfString(
            "\n", options=NSBackwardsSearch | NSLiteralSearch, range=NSRange(0, end)
        )
        start = 0 if found.location == NSNotFound else found.location + 1
        with self._caret_kept():
            storage.beginEditing()
            if end > start:
                storage.deleteCharactersInRange(NSRange(start, end - start))
            position = start
            for piece in pieces:
                storage.insertAttributedString(piece, atIndex=position)
                position += piece.length()
            storage.endEditing()
            self.output_length = position
        self._edited()

    def trim_lines(self, count):
        storage = self.text_view.textStorage
        string = storage.string
        length = self.output_length
        end = 0
        for _ in range(count):
            found = string.rangeOfString(
                "\n", options=NSLiteralSearch, range=NSRange(end, length - end)
            )
            if found.location == NSNotFound:
                end = length
                break
            end = found.location + 1
        if end:
            with self._caret_kept():
                storage.deleteCharactersInRange(NSRange(0, end))
                self.output_length -= end
        self._edited()

    def clear(self):
        with self._caret_kept():
            self.text_view.textStorage.deleteCharactersInRange(
                NSRange(0, self.output_length)
            )
            self.output_length = 0
        self._edited()

    # Typing

    def should_change(self, location, length, text):
        """Decide a user edit of ``length`` characters at ``location``.

        ``text`` is the replacement, or None for a change of attributes only.
        """
        start = self.output_length
        if text is None:
            return location >= start
        storage = self.text_view.textStorage
        if location < start:
            if not text:
                return False  # a deletion that reaches into the output
            # Typed with the caret in the output: it belongs at the end of the input.
            location, length = storage.length(), 0
        elif "\n" not in text and "\r" not in text:
            return True
        string = storage.string
        before = str(string.substringWithRange(NSRange(start, location - start)))
        after = str(string.substringFromIndex(location + length))
        text = before + text + after
        if "\n" in text or "\r" in text:
            text = self.interface._enter_input(text)
        self._set_input(text, utf16_length(text) - utf16_length(after))
        return False

    def control(self, char):
        """Send a control key's character, unless the console is read-only."""
        if not self.input_readonly:
            self.interface._enter_control(char)

    def _set_input(self, text, caret):
        storage = self.text_view.textStorage
        start = self.output_length
        storage.replaceCharactersInRange(
            NSRange(start, storage.length() - start),
            withAttributedString=NSAttributedString.alloc().initWithString(
                text, attributes=self._attributes(PLAIN)
            ),
        )
        self.text_view.setSelectedRange(NSRange(start + caret, 0))
        self.text_view.scrollRangeToVisible(NSRange(start + caret, 0))
        self._edited()

    @contextmanager
    def _caret_kept(self):
        # Output edits move the input area; a caret or selection in it moves with it.
        selection = self.text_view.selectedRange
        offset = selection.location - self.output_length
        yield
        if offset >= 0:
            self.text_view.setSelectedRange(
                NSRange(self.output_length + offset, selection.length)
            )
