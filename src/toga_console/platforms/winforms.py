from collections import deque
from contextlib import contextmanager
from ctypes import WINFUNCTYPE, c_wchar_p, windll
from ctypes.wintypes import HWND, LPARAM, UINT, WPARAM

from System.Drawing import Font, FontStyle
from toga.handlers import WeakrefCallable
from toga_winforms.colors import native_color
from toga_winforms.widgets.multilinetextinput import MultilineTextInput

# Bindings of their own, so the argument types do not change Toga's SendMessageW: a
# negative line count passes as a signed LPARAM, and a string as a pointer to it.
SendMessage = WINFUNCTYPE(LPARAM, HWND, UINT, WPARAM, LPARAM)(
    ("SendMessageW", windll.user32)
)
SendStringMessage = WINFUNCTYPE(LPARAM, HWND, UINT, WPARAM, c_wchar_p)(
    ("SendMessageW", windll.user32)
)
WM_SETREDRAW = 0x000B
EM_LINESCROLL = 0x00B6
EM_REPLACESEL = 0x00C2
EM_GETFIRSTVISIBLELINE = 0x00CE
EM_SETUNDOLIMIT = 0x0452


def utf16_length(text):
    # Rich edit positions count UTF-16 code units, so an astral character is two.
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


class Console(MultilineTextInput):
    def create(self):
        super().create()
        native = self.native
        native.ReadOnly = True
        native.DetectUrls = False  # console output is not a document
        native.HandleCreated += WeakrefCallable(self.winforms_handle_created)
        self._disable_undo()

        # The view's text is never read back: each committed line's length, in
        # positions, is kept here instead, so the tail and a trim are found without
        # copying the whole text out of the control.
        self.line_lengths = deque()
        self.tail_length = 0
        self.length = 0
        self.fonts = {}

    def winforms_handle_created(self, sender, event):
        self._disable_undo()  # the setting belongs to the native window

    def _disable_undo(self):
        # Every edit would otherwise stay in the undo buffer for good.
        SendMessage(self._hwnd(), EM_SETUNDOLIMIT, 0, 0)

    def _hwnd(self):
        return HWND(self.native.Handle.ToInt64())

    # Inherited methods that must keep the console's state in sync

    def set_readonly(self, value):
        self.native.ReadOnly = True  # a console is never editable

    def winforms_text_changed(self, sender, event):
        # The edits below fire TextChanged; the interface reports its own on_change
        # once per flush.
        pass

    def get_value(self):
        # The model's text has no carriage returns, so any here are line breaks.
        return super().get_value().replace("\r\n", "\n").replace("\r", "\n")

    def scale_font(self):
        super().scale_font()
        self.fonts = {}

    # Console backend contract

    def replace_tail(self, runs):
        # Build every piece first, so nothing can raise midway through the edit.
        pieces = [
            (text, self._font(style), self._run_color(style)) for text, style in runs
        ]
        with self._editing():
            self._delete(self.length - self.tail_length, self.length)
            for text, font, color in pieces:
                self._append(text, font, color)
            lines = "".join(text for text, _style in runs).split("\n")
            for line in lines[:-1]:
                self.line_lengths.append(utf16_length(line) + 1)
            self.tail_length = utf16_length(lines[-1])
        self._update_placeholder()

    def trim_lines(self, count):
        if count > len(self.line_lengths):
            # Past the last newline the rest goes too, as on the other backends.
            end = self.length
            self.line_lengths.clear()
            self.tail_length = 0
        else:
            end = sum(self.line_lengths.popleft() for _ in range(count))
        if end:
            with self._editing():
                self._delete(0, end)
        self._update_placeholder()

    def clear(self):
        if not self._placeholder_visible:
            self.native.Clear()
        self.line_lengths.clear()
        self.tail_length = 0
        self.length = 0
        self._update_placeholder()

    # Editing

    @contextmanager
    def _editing(self):
        # Each edit moves the selection, and the control scrolls to it, so the view
        # would jump to the end on every flush, follow or not. Painting stops while
        # the edits run; then the user's selection and first visible line return,
        # shifted for what was deleted above them. Following is the interface's job.
        native = self.native
        hwnd = self._hwnd()
        SendMessage(hwnd, WM_SETREDRAW, 0, 0)
        if self._placeholder_visible:
            native.Text = ""
            self._set_placeholder_visible(False)
        selection_start = native.SelectionStart
        selection_end = selection_start + native.SelectionLength
        self.first_line = SendMessage(hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
        self.deletions = []
        try:
            yield
        finally:
            for start, end in self.deletions:
                selection_start = shift(selection_start, start, end)
                selection_end = shift(selection_end, start, end)
            native.Select(selection_start, selection_end - selection_start)
            moved = self.first_line - SendMessage(hwnd, EM_GETFIRSTVISIBLELINE, 0, 0)
            if moved:
                SendMessage(hwnd, EM_LINESCROLL, 0, moved)
            SendMessage(hwnd, WM_SETREDRAW, 1, 0)
            native.Invalidate()

    def _delete(self, start, end):
        if end <= start:
            return
        native = self.native
        if start == 0:
            # Lines wrap, so a deleted line can be several lines on screen.
            self.first_line = max(0, self.first_line - native.GetLineFromCharIndex(end))
        native.Select(start, end - start)
        # Assigning "" to SelectedText sends WM_CLEAR, which a read-only control
        # ignores. EM_REPLACESEL is not blocked by read-only, and 0 keeps it out of
        # the undo buffer.
        SendStringMessage(self._hwnd(), EM_REPLACESEL, 0, "")
        self.deletions.append((start, end))
        self.length -= end - start

    def _append(self, text, font, color):
        native = self.native
        native.Select(self.length, 0)
        native.SelectionFont = font
        native.SelectionColor = color
        native.SelectedText = text
        self.length += utf16_length(text)

    def _update_placeholder(self):
        # The base shows its placeholder as the control's own text, and only from
        # set_value, which the console never calls.
        if not self.length and not self.native.ContainsFocus:
            self._set_placeholder_visible(True)

    # Styles

    def _font(self, style):
        key = (style.bold, style.italic)
        try:
            return self.fonts[key]
        except KeyError:
            pass
        font = self.native.Font
        if style.bold or style.italic:
            traits = font.Style
            if style.bold:
                traits |= FontStyle.Bold
            if style.italic:
                traits |= FontStyle.Italic
            font = Font(font, traits)
        self.fonts[key] = font
        return font

    def _run_color(self, style):
        # MultilineTextInput keeps the widget's own text colour in _color.
        return self._color if style.color is None else native_color(style.color)


def shift(position, start, end):
    """Where ``position`` lands once the range from ``start`` to ``end`` is deleted."""
    if position <= start:
        return position
    if position < end:
        return start
    return position - (end - start)
