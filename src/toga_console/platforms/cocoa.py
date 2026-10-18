from contextlib import contextmanager
from ctypes import c_void_p

from rubicon.objc import (
    NSObject,
    NSRange,
    ObjCInstance,
    objc_method,
    objc_property,
    send_super,
)
from toga_cocoa.colors import native_color
from toga_cocoa.libs import (
    NSAttributedString,
    NSBezelBorder,
    NSColor,
    NSFontAttributeName,
    NSFontManager,
    NSForegroundColorAttributeName,
    NSMutableDictionary,
    NSScrollView,
    NSViewHeightSizable,
    NSViewWidthSizable,
)
from toga_cocoa.widgets.multilinetextinput import MultilineTextInput, TogaTextView

from ..model import PLAIN

# NSStringCompareOptions
NSLiteralSearch = 2  # matches a newline even when a combining character follows it
NSBackwardsSearch = 4
# NSFontTraitMask
NSItalicFontMask = 1 << 0
NSBoldFontMask = 1 << 1
# NSNotFound is NSIntegerMax.
NSNotFound = 0x7FFFFFFFFFFFFFFF
# NSEventModifierFlags
NSEventModifierFlagShift = 1 << 17
NSEventModifierFlagControl = 1 << 18
NSEventModifierFlagOption = 1 << 19
NSEventModifierFlagCommand = 1 << 20
CHORD_FLAGS = (
    NSEventModifierFlagShift
    | NSEventModifierFlagControl
    | NSEventModifierFlagOption
    | NSEventModifierFlagCommand
)


def utf16_length(text):
    """The length of ``text`` as NSString counts it."""
    return len(text.encode("utf-16-le")) // 2


# The console's control keys, by the key pressed with Control.
CONTROL_KEYS = {"c": "\x03", "d": "\x04"}


class TogaConsoleTextView(TogaTextView):
    """Sends Ctrl-C and Ctrl-D to the console instead of NSTextView's bindings.

    NSTextView binds Ctrl-D to deleting forward and Ctrl-C to nothing.
    """

    @objc_method
    def keyDown_(self, event) -> None:
        if event.modifierFlags & CHORD_FLAGS == NSEventModifierFlagControl:
            char = CONTROL_KEYS.get(str(event.charactersIgnoringModifiers))
            if char is not None:
                self.impl.control(char)
                return
        send_super(__class__, self, "keyDown:", event, argtypes=[c_void_p])


class TogaConsoleDelegate(NSObject):
    """Keeps typing inside the input area, and enters each line typed there."""

    impl = objc_property(object, weak=True)

    @objc_method
    def textView_shouldChangeTextInRange_replacementString_(
        self, text_view, affected: NSRange, replacement
    ) -> bool:
        return self.impl.should_change(
            affected.location,
            affected.length,
            None if replacement is None else str(replacement),
        )

    @objc_method
    def textView_shouldChangeTypingAttributes_toAttributes_(
        self, text_view, old, new
    ) -> ObjCInstance:
        # Typed text is plain, whatever the output it follows was drawn in.
        return self.impl._attributes(PLAIN)


class Console(MultilineTextInput):
    """The native text is the output, then the input area: what the user is typing.

    ``output_length`` is where the input area starts, in NSString units. The output
    calls edit only the text before it, and the delegate lets the user edit only the
    text after it.
    """

    def create(self):
        # Toga's create(), with the text view that catches Ctrl-C and Ctrl-D.
        self.native = NSScrollView.alloc().init()
        self.native.hasVerticalScroller = True
        self.native.hasHorizontalScroller = False
        self.native.autohidesScrollers = False
        self.native.borderType = NSBezelBorder

        text = self.native_text = TogaConsoleTextView.alloc().init()
        text.interface = self.interface
        text.impl = self
        text.verticallyResizable = True
        text.horizontallyResizable = False
        text.usesAdaptiveColorMappingForDarkAppearance = True
        text.setAutomaticQuoteSubstitutionEnabled(False)
        text.setAutomaticDashSubstitutionEnabled(False)
        text.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable
        self.native.documentView = text
        self.add_constraints()

        text.editable = False
        text.selectable = True
        text.allowsUndo = False
        # Console output is not prose.
        text.setContinuousSpellCheckingEnabled(False)
        text.setGrammarCheckingEnabled(False)
        text.setAutomaticSpellingCorrectionEnabled(False)
        text.setAutomaticTextReplacementEnabled(False)
        text.setAutomaticTextCompletionEnabled(False)

        # The delegate is a separate object, so TogaTextView's textDidChange_ never
        # runs and typing does not fire on_change, which reports output.
        self.delegate = TogaConsoleDelegate.alloc().init()
        self.delegate.impl = self
        text.delegate = self.delegate

        # NSTextView.font and .textColor read back the *first character's* attributes
        # once the storage is styled, so the base style is tracked here instead.
        self.base_font = text.font
        self.base_color = NSColor.textColor
        self.attributes = {}
        self.output_length = 0
        self.input_readonly = True

    # Inherited methods that must keep the base style in sync

    def get_readonly(self):
        return self.input_readonly

    def set_readonly(self, value):
        self.input_readonly = bool(value)
        self.native_text.editable = not self.input_readonly

    def set_enabled(self, value):
        self.native_text.selectable = value
        self.native_text.editable = value and not self.input_readonly

    def get_value(self):
        string = self.native_text.textStorage.string
        return str(string.substringToIndex(self.output_length))

    def set_font(self, font):
        super().set_font(font)
        self.base_font = font._impl.native
        self.attributes = {}
        storage = self.native_text.textStorage
        storage.addAttribute(
            NSFontAttributeName,
            value=self.base_font,
            range=NSRange(0, storage.length()),
        )

    def set_color(self, value):
        super().set_color(value)
        self.base_color = native_color(value) or NSColor.textColor
        self.attributes = {}

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
        storage = self.native_text.textStorage
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

    def trim_lines(self, count):
        storage = self.native_text.textStorage
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

    def clear(self):
        with self._caret_kept():
            self.native_text.textStorage.deleteCharactersInRange(
                NSRange(0, self.output_length)
            )
            self.output_length = 0

    # Typing

    def should_change(self, location, length, text):
        """Decide a user edit of ``length`` characters at ``location``."""
        start = self.output_length
        if text is None:  # attributes only
            return location >= start
        storage = self.native_text.textStorage
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
        if not self.input_readonly:
            self.interface._enter_control(char)

    def _set_input(self, text, caret):
        storage = self.native_text.textStorage
        start = self.output_length
        storage.replaceCharactersInRange(
            NSRange(start, storage.length() - start),
            withAttributedString=NSAttributedString.alloc().initWithString(
                text, attributes=self._attributes(PLAIN)
            ),
        )
        self.native_text.setSelectedRange(NSRange(start + caret, 0))
        self.native_text.scrollRangeToVisible(NSRange(start + caret, 0))

    @contextmanager
    def _caret_kept(self):
        # Output edits move the input area; a caret or selection in it moves with it.
        selection = self.native_text.selectedRange
        offset = selection.location - self.output_length
        yield
        if offset >= 0:
            self.native_text.setSelectedRange(
                NSRange(self.output_length + offset, selection.length)
            )

    # Styles

    def _attributes(self, style):
        try:
            return self.attributes[style]
        except KeyError:
            pass
        attributes = NSMutableDictionary.alloc().init()
        font = self.base_font
        traits = (NSBoldFontMask if style.bold else 0) | (
            NSItalicFontMask if style.italic else 0
        )
        if traits:
            font = NSFontManager.sharedFontManager.convertFont(font, toHaveTrait=traits)
        attributes[NSFontAttributeName] = font
        attributes[NSForegroundColorAttributeName] = (
            self.base_color if style.color is None else native_color(style.color)
        )
        self.attributes[style] = attributes
        return attributes
