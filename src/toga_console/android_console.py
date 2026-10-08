import weakref

from android.graphics import Typeface
from android.text import Selection, SpannableString, Spanned, TextUtils
from android.text.style import ForegroundColorSpan, StyleSpan
from java import dynamic_proxy
from java.lang import Runnable
from toga_android.colors import native_color
from toga_android.widgets.base import suppress_reference_error
from toga_android.widgets.multilinetextinput import MultilineTextInput


class TogaBottomScroller(dynamic_proxy(Runnable)):
    def __init__(self, impl):
        super().__init__()
        self.impl = weakref.proxy(impl)

    def run(self):
        with suppress_reference_error():
            self.impl._scroll_to_bottom_now()


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
        self.bottom_scroller = TogaBottomScroller(self)

    def set_readonly(self, readonly):
        # Always read-only. The base would make the view unfocusable, which also
        # ends text selection.
        pass

    def _on_change(self):
        # The text watcher fires for the edits below; the interface reports its own
        # on_change once per flush.
        pass

    def scroll_to_bottom(self):
        # Scroll the view, never the selection: the base moves the cursor to the
        # end, and the view would then follow it (see _drop_caret). Posted, so it
        # runs after the next layout pass.
        self.native.post(self.bottom_scroller)

    def _scroll_to_bottom_now(self):
        native = self.native
        layout = native.getLayout()
        if layout is None:
            return
        bottom = (
            layout.getLineTop(layout.getLineCount())
            + native.getPaddingTop()
            + native.getPaddingBottom()
        )
        native.scrollTo(0, max(0, bottom - native.getHeight()))

    # Console backend contract

    def replace_tail(self, runs):
        editable = self.native.getText()
        self._drop_caret(editable)
        start = TextUtils.lastIndexOf(editable, "\n") + 1
        editable.delete(start, editable.length())
        for text, style in runs:
            editable.append(self._spannable(text, style))

    def trim_lines(self, count):
        editable = self.native.getText()
        self._drop_caret(editable)
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

    # Selection and styles

    def _drop_caret(self, editable):
        # TextView scrolls the selection into view after every edit, and output
        # appended at a caret carries it along, so a caret would pull the view to
        # itself on every flush, follow or not. A selected range is the user's.
        start = Selection.getSelectionStart(editable)
        if start >= 0 and start == Selection.getSelectionEnd(editable):
            Selection.removeSelection(editable)

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
