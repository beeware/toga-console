import sys
import weakref
from contextlib import suppress

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont,
    QKeySequence,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import QPlainTextEdit
from toga_qt.colors import native_color
from toga_qt.widgets.multilinetextinput import MultilineTextInput

# QTextCursor.insertText starts a new block at U+2029 and at Qt's frame markers as
# well as at "\n". Each console line must be exactly one block, so those characters
# are drawn as a line break inside the block and as the replacement character.
BLOCK_BREAKS = str.maketrans(
    {"\u2029": "\u2028", "\ufdd0": "\ufffd", "\ufdd1": "\ufffd"}
)

# Qt on macOS calls the Command key Control, and the Control key Meta.
CONTROL = (
    Qt.KeyboardModifier.MetaModifier
    if sys.platform == "darwin"
    else Qt.KeyboardModifier.ControlModifier
)
CHORD_MODIFIERS = (
    Qt.KeyboardModifier.ShiftModifier
    | Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)
# The console's control keys, by the key pressed with Control.
CONTROL_KEYS = {Qt.Key.Key_C: "\x03", Qt.Key.Key_D: "\x04"}
RETURN_KEYS = {Qt.Key.Key_Return, Qt.Key.Key_Enter}
# Context menu actions that delete the selection.
DELETING_ACTIONS = {"edit-cut", "edit-delete"}


def utf16_length(text):
    """The length of ``text`` as a QTextDocument counts positions."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


class TogaConsoleTextEdit(QPlainTextEdit):
    """Keeps typing inside the input area, and sends Ctrl-C and Ctrl-D.

    Key presses, pastes, and drops go through the backend first, which handles any
    that would touch the output or end a line, and lets the rest through.
    """

    def __init__(self, impl):
        super().__init__()
        self.impl = weakref.proxy(impl)

    def keyPressEvent(self, event):
        try:
            handled = self.impl._key_press(event)
        except ReferenceError:
            handled = False
        if not handled:
            super().keyPressEvent(event)

    def inputMethodEvent(self, event):
        # Text composed in an input method is typed at the caret.
        with suppress(ReferenceError):
            self.impl._caret_to_input()
        super().inputMethodEvent(event)

    def insertFromMimeData(self, source):
        # A paste or a drop.
        with suppress(ReferenceError):
            self.impl._edit(source.text())

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        try:
            protected = self.impl._selection_touches_output()
        except ReferenceError:
            protected = False
        for action in menu.actions():
            if protected and action.objectName() in DELETING_ACTIONS:
                action.setEnabled(False)
        menu.exec(event.globalPos())
        menu.deleteLater()


class Console(MultilineTextInput):
    """The text is the output, then the input area: what the user is typing.

    ``output_length`` is where the input area starts, in document positions. The
    output calls edit only the text before it, and the text edit lets the user edit
    only the text after it.
    """

    def create(self):
        # QPlainTextEdit rather than the base's QTextEdit: it lays out a block at a
        # time, which keeps a long scrollback fast, and every inherited method works
        # on either. Unlike the base, textChanged is not connected: the interface
        # reports its own on_change once per flush.
        self.native = TogaConsoleTextEdit(self)
        self.native.setUndoRedoEnabled(False)
        self.native.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # The text is drawn in the Text role on the Base role, not the widget's own
        # foreground and background roles, which the base class would set.
        self._foreground_color_role = QPalette.ColorRole.Text
        self._background_color_role = QPalette.ColorRole.Base
        self.formats = {}
        self.output_length = 0
        self.input_readonly = True

    # Inherited methods

    def get_readonly(self):
        return self.input_readonly

    def set_readonly(self, value):
        # A read-only view keeps mouse selection and copying, and draws no caret.
        self.input_readonly = bool(value)
        self.native.setReadOnly(self.input_readonly)

    def get_value(self):
        return self._text(0, self.output_length)

    # Console backend contract

    def replace_tail(self, runs):
        document = self.native.document()
        end = self.output_length
        cursor = QTextCursor(document)
        cursor.setPosition(document.findBlock(end).position())
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        # A cursor at the start of the input moves along with text inserted where it
        # stands, so the user's caret stays at the start of the input.
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        for text, style in runs:
            cursor.insertText(text.translate(BLOCK_BREAKS), self._format(style))
        cursor.endEditBlock()
        self.output_length = cursor.position()

    def trim_lines(self, count):
        document = self.native.document()
        block = document.findBlockByNumber(count)
        end = self.output_length
        if block.isValid() and block.position() < end:
            end = block.position()
        if end:
            cursor = QTextCursor(document)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self.output_length -= end

    def clear(self):
        cursor = QTextCursor(self.native.document())
        cursor.setPosition(self.output_length, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self.output_length = 0

    # Typing

    def _key_press(self, event):
        """Handle a key press the text edit should not; True if handled."""
        if self.input_readonly:
            return False  # Ctrl-C copies as usual
        key = event.key()
        modifiers = event.modifiers() & CHORD_MODIFIERS
        char = CONTROL_KEYS.get(key)
        if char is not None and modifiers == CONTROL:
            self.interface._enter_control(char)
            return True
        if key in RETURN_KEYS:
            self._edit("\n")
            return True

        cursor = self.native.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        output_end = self.output_length
        if (
            key in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete}
            or event.matches(QKeySequence.StandardKey.DeleteStartOfWord)
            or event.matches(QKeySequence.StandardKey.DeleteEndOfWord)
            or event.matches(QKeySequence.StandardKey.DeleteEndOfLine)
            or event.matches(QKeySequence.StandardKey.DeleteCompleteLine)
            or event.matches(QKeySequence.StandardKey.Cut)
        ):
            if start != end:
                return start < output_end  # a selection reaching into the output
            backward = key == Qt.Key.Key_Backspace or event.matches(
                QKeySequence.StandardKey.DeleteStartOfWord
            )
            if event.matches(QKeySequence.StandardKey.DeleteCompleteLine):
                self._delete_input(output_end, self._end())
                return True
            if not backward:
                return start < output_end
            # A backward deletion stops at the start of the input.
            target = QTextCursor(cursor)
            if event.matches(QKeySequence.StandardKey.DeleteStartOfWord):
                target.movePosition(QTextCursor.MoveOperation.PreviousWord)
            elif modifiers:
                target.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            else:
                target.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
            if target.position() >= output_end:
                return False
            if start > output_end:
                self._delete_input(output_end, start)
            return True

        if event.text() and event.text().isprintable():
            # Typing: with the caret in the output, it goes at the end of the input.
            self._caret_to_input()
            self.native.setCurrentCharFormat(QTextCharFormat())
        return False

    def _caret_to_input(self):
        cursor = self.native.textCursor()
        if cursor.selectionStart() < self.output_length:
            cursor.setPosition(self._end())
            self.native.setTextCursor(cursor)

    def _selection_touches_output(self):
        cursor = self.native.textCursor()
        return cursor.hasSelection() and cursor.selectionStart() < self.output_length

    def _edit(self, text):
        """Type ``text`` at the caret, entering every line it completes."""
        if self.input_readonly or not text:
            return
        cursor = self.native.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        output_end = self.output_length
        document_end = self._end()
        if start < output_end:
            start = end = document_end
        before = self._text(output_end, start)
        after = self._text(end, document_end)
        text = before + text + after
        if any(char in text for char in "\n\r\u2028\u2029"):
            text = text.replace("\u2028", "\n").replace("\u2029", "\n")
            text = self.interface._enter_input(text)
        self._set_input(text, utf16_length(text) - utf16_length(after))

    def _delete_input(self, start, end):
        cursor = QTextCursor(self.native.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()

    def _set_input(self, text, caret):
        start = self.output_length
        cursor = QTextCursor(self.native.document())
        cursor.setPosition(start)
        cursor.setPosition(self._end(), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text.translate(BLOCK_BREAKS), QTextCharFormat())
        cursor.setPosition(start + caret)
        self.native.setTextCursor(cursor)
        self.native.ensureCursorVisible()

    def _end(self):
        # The document's last position is before its implicit final separator.
        return self.native.document().characterCount() - 1

    def _text(self, start, end):
        # toPlainText would also turn non-breaking spaces into spaces. Positions
        # count UTF-16 code units.
        units = self.native.document().toRawText().encode("utf-16-le", "surrogatepass")
        text = units[2 * start : 2 * end].decode("utf-16-le", "surrogatepass")
        return text.replace("\u2029", "\n")

    # Styles

    def _format(self, style):
        try:
            return self.formats[style]
        except KeyError:
            pass
        # A format only sets what the style changes; the rest comes from the widget's
        # font and palette, so a later font or colour change still reaches it.
        format = QTextCharFormat()
        if style.color is not None:
            format.setForeground(native_color(style.color))
        if style.bold:
            format.setFontWeight(QFont.Weight.Bold)
        if style.italic:
            format.setFontItalic(True)
        self.formats[style] = format
        return format
