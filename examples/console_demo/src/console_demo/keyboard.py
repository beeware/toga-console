"""Simulated typing into a console's native view, for the demo's self-test.

Each keyboard drives the view through the path a user's key presses take, so the
self-test exercises the backend's own handling of typing, Backspace, Return, and
the Control keys.
"""

from toga.platform import current_platform


def native_keyboard(console):
    """A keyboard for the console's native view, or None on other platforms."""
    keyboard = {
        "macOS": MacKeyboard,
        "iOS": IOSKeyboard,
        "android": AndroidKeyboard,
    }.get(current_platform)
    return None if keyboard is None else keyboard(console._impl)


class MacKeyboard:
    def __init__(self, impl):
        self.native = impl.native_text

    def type(self, text):
        self.native.insertText(text)

    def enter(self):
        self.native.insertNewline(None)  # what Return sends

    def backspace(self):
        self.native.deleteBackward(None)

    def caret_to(self, index):
        from rubicon.objc import NSRange

        self.native.setSelectedRange(NSRange(index, 0))

    def control(self, key):
        from rubicon.objc import NSPoint, ObjCClass

        self.native.keyDown(
            ObjCClass("NSEvent").keyEventWithType(
                10,  # NSEventTypeKeyDown
                location=NSPoint(0, 0),
                modifierFlags=1 << 18,  # NSEventModifierFlagControl
                timestamp=0.0,
                windowNumber=0,
                context=None,
                characters=chr(ord(key) - ord("a") + 1),
                charactersIgnoringModifiers=key,
                isARepeat=False,
                keyCode={"c": 8, "d": 2}[key],
            )
        )

    def text(self):
        return str(self.native.string)


class IOSKeyboard:
    def __init__(self, impl):
        self.native = impl.native

    # A programmatic insertText or deleteBackward skips the delegate, which UIKit
    # asks first for a key press; ask it the same way.

    def _allowed(self, location, length, text):
        from rubicon.objc import NSRange

        return self.native.textView_shouldChangeTextInRange_replacementText_(
            self.native, NSRange(location, length), text
        )

    def type(self, text):
        selection = self.native.selectedRange
        if self._allowed(selection.location, selection.length, text):
            self.native.insertText(text)

    def enter(self):
        self.type("\n")  # what Return sends

    def backspace(self):
        selection = self.native.selectedRange
        location, length = selection.location, selection.length
        if not length:
            location, length = location - 1, 1
        if location >= 0 and self._allowed(location, length, ""):
            self.native.deleteBackward()

    def caret_to(self, index):
        from rubicon.objc import NSRange

        self.native.setSelectedRange(NSRange(index, 0))

    def control(self, key):
        # A hardware keyboard's chord runs the key command the view offers for it.
        for command in self.native.keyCommands:
            if str(command.input) == key:
                self.native.performSelector(command.action, withObject=command)
                return
        raise AssertionError(f"no key command for Ctrl-{key}")

    def text(self):
        return str(self.native.textStorage.string)


class AndroidKeyboard:
    def __init__(self, impl):
        self.native = impl.native

    def type(self, text):
        # What an input method's commitText does: replace the selection.
        from android.text import Selection

        editable = self.native.getText()
        start = Selection.getSelectionStart(editable)
        end = Selection.getSelectionEnd(editable)
        if start < 0:
            start = end = editable.length()
        editable.replace(min(start, end), max(start, end), text)

    def _press(self, key_code, meta_state=0):
        from android.os import SystemClock
        from android.view import KeyEvent

        now = SystemClock.uptimeMillis()
        for action in (KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP):
            self.native.dispatchKeyEvent(
                KeyEvent(now, now, action, key_code, 0, meta_state)
            )

    def enter(self):
        from android.view import KeyEvent

        self._press(KeyEvent.KEYCODE_ENTER)

    def backspace(self):
        from android.view import KeyEvent

        self._press(KeyEvent.KEYCODE_DEL)

    def caret_to(self, index):
        self.native.setSelection(index)

    def control(self, key):
        from android.view import KeyEvent

        self._press(
            {"c": KeyEvent.KEYCODE_C, "d": KeyEvent.KEYCODE_D}[key],
            KeyEvent.META_CTRL_ON | KeyEvent.META_CTRL_LEFT_ON,
        )

    def text(self):
        return str(self.native.getText())
