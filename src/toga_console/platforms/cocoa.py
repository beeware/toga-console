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
from .textkit import CONTROL_KEYS, TextKitConsole

# NSFontTraitMask
NSItalicFontMask = 1 << 0
NSBoldFontMask = 1 << 1
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


class Console(TextKitConsole, MultilineTextInput):
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

    @property
    def text_view(self):
        return self.native_text

    # Inherited methods that must keep the base style in sync

    def get_readonly(self):
        return self.input_readonly

    def set_readonly(self, value):
        self.input_readonly = bool(value)
        self.native_text.editable = not self.input_readonly

    def set_enabled(self, value):
        self.native_text.selectable = value
        self.native_text.editable = value and not self.input_readonly

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
