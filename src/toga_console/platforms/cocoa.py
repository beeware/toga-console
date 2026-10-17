from rubicon.objc import NSRange
from toga_cocoa.colors import native_color
from toga_cocoa.libs import (
    NSAttributedString,
    NSColor,
    NSFontAttributeName,
    NSFontManager,
    NSForegroundColorAttributeName,
    NSMutableDictionary,
)
from toga_cocoa.widgets.multilinetextinput import MultilineTextInput

# NSStringCompareOptions
NSLiteralSearch = 2  # matches a newline even when a combining character follows it
NSBackwardsSearch = 4
# NSFontTraitMask
NSItalicFontMask = 1 << 0
NSBoldFontMask = 1 << 1
# NSNotFound is NSIntegerMax.
NSNotFound = 0x7FFFFFFFFFFFFFFF


class Console(MultilineTextInput):
    def create(self):
        super().create()
        text = self.native_text
        text.editable = False
        text.selectable = True
        text.allowsUndo = False
        # Console output is not prose.
        text.setContinuousSpellCheckingEnabled(False)
        text.setGrammarCheckingEnabled(False)
        text.setAutomaticSpellingCorrectionEnabled(False)
        text.setAutomaticTextReplacementEnabled(False)
        text.setAutomaticTextCompletionEnabled(False)

        # NSTextView.font and .textColor read back the *first character's* attributes
        # once the storage is styled, so the base style is tracked here instead.
        self.base_font = text.font
        self.base_color = NSColor.textColor
        self.attributes = {}

    # Inherited methods that must keep the base style in sync

    def set_readonly(self, value):
        self.native_text.editable = False  # a console is never editable

    def set_enabled(self, value):
        self.native_text.selectable = value  # editing stays off

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
        length = storage.length()
        found = storage.string.rangeOfString(
            "\n", options=NSBackwardsSearch | NSLiteralSearch
        )
        start = 0 if found.location == NSNotFound else found.location + 1
        storage.beginEditing()
        if length > start:
            storage.deleteCharactersInRange(NSRange(start, length - start))
        for piece in pieces:
            storage.appendAttributedString(piece)
        storage.endEditing()

    def trim_lines(self, count):
        storage = self.native_text.textStorage
        string = storage.string
        length = storage.length()
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
            storage.deleteCharactersInRange(NSRange(0, end))

    def clear(self):
        storage = self.native_text.textStorage
        storage.deleteCharactersInRange(NSRange(0, storage.length()))

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
