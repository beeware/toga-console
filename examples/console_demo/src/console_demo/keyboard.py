"""Simulated typing into a console's native view, for the demo's self-test.

Each keyboard drives the view through the path a user's key presses take, so the
self-test exercises the backend's own handling of typing, Backspace, Return, and
the Control keys.
"""


def native_keyboard(console):
    """A keyboard for the console's native view, or None for other backends."""
    # By backend, not platform: the Qt demo runs on every desktop platform.
    backend = type(console._impl).__module__.rpartition(".")[2]
    keyboard = {
        "cocoa": MacKeyboard,
        "iOS": IOSKeyboard,
        "android": AndroidKeyboard,
        "gtk": GtkKeyboard,
        "qt": QtKeyboard,
        "winforms": WindowsKeyboard,
    }.get(backend)
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


class GtkKeyboard:
    def __init__(self, impl):
        self.view = impl.native_textview
        self.buffer = impl.buffer

    def type(self, text):
        # The key binding signal that typing and pasting use.
        self.view.emit("insert-at-cursor", text)

    def enter(self):
        self.type("\n")  # what Return inserts

    def backspace(self):
        self.view.emit("backspace")

    def caret_to(self, index):
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(index))

    def control(self, key):
        from toga_gtk.libs import Gdk, Gtk

        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.key.keyval = Gdk.keyval_from_name(key)
        event.key.state = Gdk.ModifierType.CONTROL_MASK
        event.key.window = self.view.get_window(Gtk.TextWindowType.TEXT)
        self.view.event(event)

    def text(self):
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)


class QtKeyboard:
    def __init__(self, impl):
        self.native = impl.native

    def _press(self, key, text="", modifiers=None):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QApplication

        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(self.native, QKeyEvent(kind, key, modifiers, text))

    def type(self, text):
        for char in text:
            self._press(0, char)

    def enter(self):
        from PySide6.QtCore import Qt

        self._press(Qt.Key.Key_Return, "\r")

    def backspace(self):
        from PySide6.QtCore import Qt

        self._press(Qt.Key.Key_Backspace, "\b")

    def caret_to(self, index):
        cursor = self.native.textCursor()
        cursor.setPosition(index)
        self.native.setTextCursor(cursor)

    def control(self, key):
        import sys

        from PySide6.QtCore import Qt

        # Qt on macOS calls the Control key Meta.
        modifiers = (
            Qt.KeyboardModifier.MetaModifier
            if sys.platform == "darwin"
            else Qt.KeyboardModifier.ControlModifier
        )
        self._press(
            {"c": Qt.Key.Key_C, "d": Qt.Key.Key_D}[key],
            chr(ord(key) - ord("a") + 1),
            modifiers,
        )

    def text(self):
        return self.native.document().toRawText().replace("\u2029", "\n")


class WindowsKeyboard:
    """Keys sent to the control's window, as the message loop delivers them."""

    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_CHAR = 0x0102
    VK_BACK = 0x08
    VK_RETURN = 0x0D

    def __init__(self, impl):
        self.impl = impl
        self.native = impl.native

    def _send(self, message, wparam):
        from ctypes import windll
        from ctypes.wintypes import HWND

        hwnd = HWND(self.native.Handle.ToInt64())
        windll.user32.SendMessageW(hwnd, message, wparam, 0)

    def _key(self, virtual_key, char):
        self._send(self.WM_KEYDOWN, virtual_key)
        self._send(self.WM_CHAR, ord(char))
        self._send(self.WM_KEYUP, virtual_key)

    def type(self, text):
        for char in text:
            self._send(self.WM_CHAR, ord(char))

    def enter(self):
        self._key(self.VK_RETURN, "\r")

    def backspace(self):
        self._key(self.VK_BACK, "\b")

    def caret_to(self, index):
        self.native.Select(index, 0)

    def control(self, key):
        # A sent message cannot carry the Control key's state, which Winforms reads
        # from the keyboard, so the key event goes to the console's handler.
        import System.Windows.Forms as WinForms

        keys = {"c": WinForms.Keys.C, "d": WinForms.Keys.D}[key]
        self.impl.winforms_key_down(
            self.native, WinForms.KeyEventArgs(keys | WinForms.Keys.Control)
        )

    def text(self):
        return str(self.native.Text).replace("\r\n", "\n").replace("\r", "\n")
