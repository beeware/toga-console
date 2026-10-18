from rubicon.objc import (
    SEL,
    NSArray,
    NSMutableDictionary,
    NSRange,
    ObjCClass,
    ObjCInstance,
    objc_method,
    send_message,
)
from rubicon.objc.types import NSInteger
from toga_iOS.colors import native_color
from toga_iOS.libs import (
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    UIColor,
    UIFont,
    UIFontDescriptorTraitBold,
    UIFontDescriptorTraitItalic,
    UILabel,
)
from toga_iOS.widgets.multilinetextinput import (
    MultilineTextInput,
    TogaMultilineTextView,
)

from ..model import PLAIN
from .textkit import CONTROL_KEYS, TextKitConsole

UIColor.declare_class_property("labelColor")
UIKeyCommand = ObjCClass("UIKeyCommand")

# UITextAutocapitalizationType / UITextAutocorrectionType / UITextSpellCheckingType /
# UITextSmartQuotesType / UITextSmartDashesType / UITextSmartInsertDeleteType
UITextAutocapitalizationTypeNone = 0
UITextAutocorrectionTypeNo = 1
UITextSpellCheckingTypeNo = 1
UITextSmartQuotesTypeNo = 1
UITextSmartDashesTypeNo = 1
UITextSmartInsertDeleteTypeNo = 1
# UIKeyModifierFlags
UIKeyModifierControl = 1 << 18


class TogaConsoleTextView(TogaMultilineTextView):
    """Keeps typing inside the input area, and sends Ctrl-C and Ctrl-D.

    The view is its own delegate, as in Toga. Ctrl-C and Ctrl-D come from a hardware
    keyboard as key commands; the on-screen keyboard has no Control key.
    """

    @objc_method
    def textView_shouldChangeTextInRange_replacementText_(
        self, text_view, affected: NSRange, replacement
    ) -> bool:
        return self.impl.should_change(
            affected.location,
            affected.length,
            None if replacement is None else str(replacement),
        )

    @objc_method
    def textViewDidChange_(self, text_view) -> None:
        # Typing: keep the placeholder in sync. Not on_change, which reports output.
        self.impl._edited()

    @objc_method
    def textViewDidChangeSelection_(self, text_view) -> None:
        # UITextView takes its typing attributes from the text before the caret.
        self.impl._plain_typing()

    @objc_method
    def keyCommands(self) -> ObjCInstance:
        return self.impl.key_commands

    @objc_method
    def consoleControl_(self, command) -> None:
        self.impl.control(CONTROL_KEYS[str(command.input)])


class Console(TextKitConsole, MultilineTextInput):
    def create(self):
        # Toga's create(), with the text view that guards the output.
        self.native = TogaConsoleTextView.alloc().init()
        self.native.interface = self.interface
        self.native.impl = self
        self.native.delegate = self.native
        self.placeholder_label = UILabel.alloc().init()
        self.placeholder_label.translatesAutoresizingMaskIntoConstraints = False
        self.placeholder_label.font = self.native.font
        self.placeholder_label.alpha = 0.5
        self.native.addSubview(self.placeholder_label)
        self.constrain_placeholder_label()
        self.native.placeholder_label = self.placeholder_label
        self.add_constraints()

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

        commands = []
        for key in CONTROL_KEYS:
            command = UIKeyCommand.keyCommandWithInput(
                key,
                modifierFlags=UIKeyModifierControl,
                action=SEL("consoleControl:"),
            )
            # Before iOS 15 the text view's own key handling always wins.
            if command.respondsToSelector(SEL("setWantsPriorityOverSystemBehavior:")):
                command.wantsPriorityOverSystemBehavior = True
            commands.append(command)
        self.key_commands = NSArray.arrayWithArray(commands)

        # UITextView.font and .textColor read back the *first character's* attributes
        # once the storage is styled, so the base style is tracked here instead.
        self.base_font = self.native.font
        self.base_color = UIColor.labelColor
        self.attributes = {}
        self.output_length = 0
        self.input_readonly = True

    @property
    def text_view(self):
        return self.native

    # Inherited methods that must keep the base style and the placeholder in sync

    def get_readonly(self):
        return self.input_readonly

    def set_readonly(self, value):
        self.input_readonly = bool(value)
        self.native.editable = not self.input_readonly

    def set_enabled(self, value):
        # A disabled console loses selection and editing.
        self.native.selectable = value
        self.native.editable = value and not self.input_readonly

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
        self._plain_typing()

    def set_color(self, value):
        super().set_color(value)
        self.base_color = native_color(value) or UIColor.labelColor
        self.attributes = {}
        self._plain_typing()

    def scroll_to_bottom(self):
        length = self.native.textStorage.length()
        if length:
            self.native.scrollRangeToVisible(NSRange(length - 1, 1))

    # Placeholder and styles

    def _edited(self):
        # The base class hides its placeholder label from set_value, which the console
        # never calls, so every edit keeps it in sync instead.
        self.placeholder_label.setHidden(
            self.native.textStorage.length() > 0 or self.native.isFirstResponder
        )
        self._plain_typing()

    def _plain_typing(self):
        self.native.typingAttributes = self._attributes(PLAIN)

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
