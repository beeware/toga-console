from rubicon.objc import NSMutableDictionary, NSRange, send_message
from rubicon.objc.types import NSInteger
from toga_iOS.colors import native_color
from toga_iOS.libs import (
    NSAttributedString,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    UIColor,
    UIFont,
    UIFontDescriptorTraitBold,
    UIFontDescriptorTraitItalic,
)
from toga_iOS.widgets.multilinetextinput import MultilineTextInput

UIColor.declare_class_property("labelColor")

# UITextAutocapitalizationType / UITextAutocorrectionType / UITextSpellCheckingType /
# UITextSmartQuotesType / UITextSmartDashesType / UITextSmartInsertDeleteType
UITextAutocapitalizationTypeNone = 0
UITextAutocorrectionTypeNo = 1
UITextSpellCheckingTypeNo = 1
UITextSmartQuotesTypeNo = 1
UITextSmartDashesTypeNo = 1
UITextSmartInsertDeleteTypeNo = 1
# NSStringCompareOptions. Literal search matches a newline even when a combining
# character follows it; the default comparison treats the pair as one character.
NSLiteralSearch = 2
NSBackwardsSearch = 4
# NSNotFound is NSIntegerMax.
NSNotFound = 0x7FFFFFFFFFFFFFFF


class Console(MultilineTextInput):
    def create(self):
        super().create()
        self.native.editable = False
        self.native.selectable = True
        # UITextView forwards its UITextInputTraits setters to an internal object, so
        # rubicon cannot see them as properties and attribute assignment is silently
        # dropped. Send the messages directly.
        for selector, value in (
            ("setAutocapitalizationType:", UITextAutocapitalizationTypeNone),
            ("setAutocorrectionType:", UITextAutocorrectionTypeNo),
            ("setSpellCheckingType:", UITextSpellCheckingTypeNo),
            ("setSmartQuotesType:", UITextSmartQuotesTypeNo),
            ("setSmartDashesType:", UITextSmartDashesTypeNo),
            ("setSmartInsertDeleteType:", UITextSmartInsertDeleteTypeNo),
        ):
            send_message(
                self.native, selector, value, restype=None, argtypes=[NSInteger]
            )

        # UITextView.font and .textColor read back the *first character's* attributes
        # once the storage is styled, so the base style is tracked here instead.
        self.base_font = self.native.font
        self.base_color = UIColor.labelColor
        self.attributes = {}

    # Inherited methods that must keep the base style and the placeholder in sync

    def set_readonly(self, value):
        self.native.editable = False  # a console is never editable

    def set_enabled(self, value):
        # Editing stays off; a disabled console only loses selection.
        self.native.selectable = value

    def get_enabled(self):
        return self.native.isSelectable()

    def set_font(self, font):
        super().set_font(font)
        self.base_font = font._impl.native
        self.attributes = {}
        storage = self.native.textStorage
        if self.base_font is not None:
            storage.addAttribute(
                NSFontAttributeName,
                value=self.base_font,
                range=NSRange(0, storage.length()),
            )

    def set_color(self, value):
        super().set_color(value)
        self.base_color = native_color(value) or UIColor.labelColor
        self.attributes = {}

    def scroll_to_bottom(self):
        length = self.native.textStorage.length()
        if length:
            self.native.scrollRangeToVisible(NSRange(length - 1, 1))

    # Console backend contract

    def replace_tail(self, runs):
        # Build every piece first, so nothing can raise between beginEditing and
        # endEditing and leave the storage mid-edit.
        pieces = [
            NSAttributedString.alloc().initWithString(
                text, attributes=self._attributes(style)
            )
            for text, style in runs
        ]
        storage = self.native.textStorage
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
        self._update_placeholder()

    def trim_lines(self, count):
        storage = self.native.textStorage
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
        self._update_placeholder()

    def clear(self):
        storage = self.native.textStorage
        storage.deleteCharactersInRange(NSRange(0, storage.length()))
        self._update_placeholder()

    # Styles and placeholder

    def _update_placeholder(self):
        # The base class hides its placeholder label from set_value, which the console
        # never calls, so the storage edits above keep it in sync instead.
        self.placeholder_label.setHidden(self.native.textStorage.length() > 0)

    def _attributes(self, style):
        try:
            return self.attributes[style]
        except KeyError:
            pass
        attributes = NSMutableDictionary.alloc().init()
        font = self.base_font
        traits = (UIFontDescriptorTraitBold if style.bold else 0) | (
            UIFontDescriptorTraitItalic if style.italic else 0
        )
        if traits and font is not None:
            # If there is no font with the requested traits, this returns None.
            styled = UIFont.fontWithDescriptor(
                font.fontDescriptor.fontDescriptorWithSymbolicTraits(traits),
                size=font.pointSize,
            )
            if styled is not None:
                font = styled
        if font is not None:
            attributes[NSFontAttributeName] = font
        attributes[NSForegroundColorAttributeName] = (
            self.base_color if style.color is None else native_color(style.color)
        )
        self.attributes[style] = attributes
        return attributes
