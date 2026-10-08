from android.graphics import Typeface
from android.text import SpannableString, Spanned, TextUtils
from android.text.style import ForegroundColorSpan, StyleSpan
from toga_android.colors import native_color
from toga_android.widgets.multilinetextinput import MultilineTextInput


class Console(MultilineTextInput):
    def create(self):
        super().create()
        native = self.native
        # Read-only but selectable: with no key listener nothing can be typed and no
        # keyboard appears, while selectable text keeps copying and touch scrolling.
        # Clearing the key listener also sets the input type to TYPE_NULL, so no
        # suggestions or autocorrection apply. Never call setInputType() after
        # this: it installs a key listener again, and a type without the multi-line
        # flag turns the view into a single line.
        native.setKeyListener(None)
        native.setTextIsSelectable(True)
        native.setCursorVisible(False)

    def set_readonly(self, readonly):
        # Always read-only. The base would make the view unfocusable, which also
        # ends text selection.
        pass

    def _on_change(self):
        # The text watcher fires for the edits below; the interface reports its own
        # on_change once per flush.
        pass

    # Console backend contract

    def replace_tail(self, runs):
        editable = self.native.getText()
        start = TextUtils.lastIndexOf(editable, "\n") + 1
        editable.delete(start, editable.length())
        for text, style in runs:
            editable.append(self._spannable(text, style))

    def trim_lines(self, count):
        editable = self.native.getText()
        end = 0
        for _ in range(count):
            index = TextUtils.indexOf(editable, "\n", end)
            if index < 0:
                end = editable.length()
                break
            end = index + 1
        if end:
            editable.delete(0, end)

    def clear(self):
        self.native.getText().clear()

    # Styles

    def _spannable(self, text, style):
        spannable = SpannableString(text)
        end = spannable.length()
        if style.color is not None:
            spannable.setSpan(
                ForegroundColorSpan(native_color(style.color)),
                0,
                end,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        if style.bold or style.italic:
            typeface_style = (Typeface.BOLD if style.bold else Typeface.NORMAL) | (
                Typeface.ITALIC if style.italic else Typeface.NORMAL
            )
            spannable.setSpan(
                StyleSpan(typeface_style), 0, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE
            )
        return spannable
