from PySide6.QtGui import QFont, QPalette, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QPlainTextEdit
from toga_qt.colors import native_color
from toga_qt.widgets.multilinetextinput import MultilineTextInput

# QTextCursor.insertText starts a new block at U+2029 and at Qt's frame markers as
# well as at "\n". Each console line must be exactly one block, so those characters
# are drawn as a line break inside the block and as the replacement character.
BLOCK_BREAKS = str.maketrans(
    {"\u2029": "\u2028", "\ufdd0": "\ufffd", "\ufdd1": "\ufffd"}
)


class Console(MultilineTextInput):
    def create(self):
        # QPlainTextEdit rather than the base's QTextEdit: it lays out a block at a
        # time, which keeps a long scrollback fast, and every inherited method works
        # on either. Unlike the base, textChanged is not connected: the interface
        # reports its own on_change once per flush.
        self.native = QPlainTextEdit()
        self.native.setUndoRedoEnabled(False)
        self.native.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # The text is drawn in the Text role on the Base role, not the widget's own
        # foreground and background roles, which the base class would set.
        self._foreground_color_role = QPalette.ColorRole.Text
        self._background_color_role = QPalette.ColorRole.Base
        self.formats = {}

    # Inherited methods

    def set_readonly(self, value):
        # Never editable. A read-only view keeps mouse selection and copying, and
        # draws no caret.
        self.native.setReadOnly(True)

    def get_value(self):
        # toPlainText would also turn non-breaking spaces into spaces.
        return self.native.document().toRawText().replace("\u2029", "\n")

    # Console backend contract

    def replace_tail(self, runs):
        document = self.native.document()
        cursor = QTextCursor(document.lastBlock())
        cursor.movePosition(
            QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
        )
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        for text, style in runs:
            cursor.insertText(text.translate(BLOCK_BREAKS), self._format(style))
        cursor.endEditBlock()

    def trim_lines(self, count):
        document = self.native.document()
        block = document.findBlockByNumber(count)
        # The document's last position is before its implicit final separator.
        end = block.position() if block.isValid() else document.characterCount() - 1
        if end:
            cursor = QTextCursor(document)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()

    def clear(self):
        self.native.clear()

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
