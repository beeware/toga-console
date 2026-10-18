import weakref
from contextlib import contextmanager

from android.graphics import Typeface
from android.text import (
    InputFilter,
    InputType,
    Selection,
    SpannableString,
    Spanned,
    TextUtils,
)
from android.text.style import ForegroundColorSpan, StyleSpan
from android.view import KeyEvent, View
from java import dynamic_proxy, jarray
from java.lang import Runnable
from toga_android.colors import native_color
from toga_android.widgets.base import suppress_reference_error
from toga_android.widgets.multilinetextinput import MultilineTextInput

# The console's control keys, by the key pressed with Control.
CONTROL_KEYS = {KeyEvent.KEYCODE_C: "\x03", KeyEvent.KEYCODE_D: "\x04"}
EDITABLE_INPUT_TYPE = (
    InputType.TYPE_CLASS_TEXT
    | InputType.TYPE_TEXT_FLAG_MULTI_LINE
    | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
)


def utf16_length(text):
    """The length of ``text`` as a Java string counts it."""
    return len(text.encode("utf-16-le")) // 2


def substring(chars, start, end):
    """``chars[start:end]`` in Java string units, as a Python string.

    Chaquopy hands a Java String over as a Python str, which counts code points, and
    any other CharSequence as a Java object.
    """
    if isinstance(chars, str):
        units = chars.encode("utf-16-le", "surrogatepass")
        return units[2 * start : 2 * end].decode("utf-16-le", "surrogatepass")
    return str(chars.subSequence(start, end))


class TogaCall(dynamic_proxy(Runnable)):
    """Runs a call on the UI thread's next turn; a dead console ignores it."""

    def __init__(self, call):
        super().__init__()
        self.call = call

    def run(self):
        with suppress_reference_error():
            self.call()


class TogaInputFilter(dynamic_proxy(InputFilter)):
    def __init__(self, impl):
        super().__init__()
        self.impl = weakref.proxy(impl)

    def filter(self, source, start, end, dest, dstart, dend):
        with suppress_reference_error():
            return self.impl._filter(source, start, end, dest, dstart, dend)
        return None


class TogaControlKeys(dynamic_proxy(View.OnKeyListener)):
    def __init__(self, impl):
        super().__init__()
        self.impl = weakref.proxy(impl)

    def onKey(self, view, key_code, event):
        with suppress_reference_error():
            return self.impl._on_key(key_code, event)
        return False


class Console(MultilineTextInput):
    """The text is the output, then the input area: what the user is typing.

    ``output_length`` is where the input area starts, in Java string units. The
    output calls edit only the text before it, and the input filter lets the user
    edit only the text after it. Every edit to an Editable passes through its
    filters, so the console's own edits run with ``editing_output`` set, which the
    filter lets through.
    """

    def create(self):
        super().create()
        native = self.native
        self.output_length = 0
        self.input_readonly = True
        self.editing_output = False
        native.setFilters(jarray(InputFilter)([TogaInputFilter(self)]))
        # Replaces the base's listener, which reports Enter for a confirm event that
        # a multiline input does not have.
        native.setOnKeyListener(TogaControlKeys(self))
        self._apply_readonly()
        self.bottom_scroller = TogaCall(self._weak_call("_scroll_to_bottom_now"))

    def get_readonly(self):
        return self.input_readonly

    def set_readonly(self, readonly):
        if bool(readonly) != self.input_readonly:
            self.input_readonly = bool(readonly)
            self._apply_readonly()

    def _apply_readonly(self):
        native = self.native
        # setTextIsSelectable sets the text again, through the filter.
        with self._output_edit():
            if self.input_readonly:
                # Read-only but selectable: with no key listener nothing can be typed
                # and no keyboard appears, while selectable text keeps copying and
                # touch scrolling. Clearing the key listener also sets the input type
                # to TYPE_NULL, so no suggestions or autocorrection apply. The base's
                # set_readonly would make the view unfocusable, which also ends text
                # selection.
                native.setKeyListener(None)
                native.setTextIsSelectable(True)
                native.setCursorVisible(False)
            else:
                # A selectable TextView never shows the keyboard, and an editable one
                # can be selected anyway. Turning selectable off also stops a touch
                # focusing the view, and stops clicks, so those come back. The input
                # type installs a key listener; it must keep the multi-line flag, or
                # the view turns into one line.
                native.setTextIsSelectable(False)
                native.setFocusableInTouchMode(True)
                native.setClickable(True)
                native.setLongClickable(True)
                native.setInputType(EDITABLE_INPUT_TYPE)
                native.setCursorVisible(True)

    def get_value(self):
        return substring(self.native.getText(), 0, self.output_length)

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
        bottom = self._bottom()
        if bottom is not None:
            self.native.scrollTo(0, bottom)

    def _bottom(self):
        # The scroll offset that shows the end of the text; None before the first
        # layout pass.
        native = self.native
        layout = native.getLayout()
        if layout is None:
            return None
        text_bottom = (
            layout.getLineTop(layout.getLineCount())
            + native.getPaddingTop()
            + native.getPaddingBottom()
        )
        return max(0, text_bottom - native.getHeight())

    # Console backend contract

    def replace_tail(self, runs):
        editable = self.native.getText()
        self._drop_caret(editable)
        end = self.output_length
        start = TextUtils.lastIndexOf(editable, "\n", end - 1) + 1 if end else 0
        position = start
        with self._output_edit():
            editable.delete(start, end)
            # The caret, a point span, moves past text inserted where it stands, so
            # a caret at the start of the input stays there.
            for text, style in runs:
                spannable = self._spannable(text, style)
                editable.insert(position, spannable)
                position += spannable.length()
        self.output_length = position

    def trim_lines(self, count):
        editable = self.native.getText()
        self._drop_caret(editable)
        length = self.output_length
        end = 0
        for _ in range(count):
            index = TextUtils.indexOf(editable, "\n", end)
            if index < 0 or index >= length:
                end = length
                break
            end = index + 1
        if end:
            with self._output_edit():
                editable.delete(0, end)
            self.output_length -= end
            # TextView keeps its scroll offset when the text shrinks, which could
            # leave the view past the end of what remains.
            bottom = self._bottom()
            if bottom is not None and self.native.getScrollY() > bottom:
                self.native.scrollTo(0, bottom)

    def clear(self):
        with self._output_edit():
            self.native.getText().delete(0, self.output_length)
        self.output_length = 0
        self.native.scrollTo(0, 0)  # TextView would keep the old offset

    # Typing

    def _filter(self, source, start, end, dest, dstart, dend):
        """Decide an edit replacing ``dest[dstart:dend]`` with ``source[start:end]``.

        Returns None to allow the edit, or the text to use instead. An edit that
        cannot be made where it was aimed is refused, and made from the UI thread's
        next turn, since an Editable must not change inside its own filter.
        """
        if self.editing_output:
            return None
        text = substring(source, start, end)
        output_end = self.output_length
        if dstart < output_end:
            if not text:
                return dest.subSequence(dstart, dend)  # keep the output it would delete
            # Typed with the caret in the output: it belongs at the end of the input.
            before = substring(dest, output_end, dest.length())
            self._post("_apply_input", before + text, "")
            return ""
        if "\n" not in text and "\r" not in text:
            return None
        before = substring(dest, output_end, dstart)
        after = substring(dest, dend, dest.length())
        self._post("_apply_input", before + text + after, after)
        return ""

    def _apply_input(self, text, after):
        if "\n" in text or "\r" in text:
            text = self.interface._enter_input(text)
        editable = self.native.getText()
        start = self.output_length
        with self._output_edit():
            editable.replace(start, editable.length(), text)
        self.native.setSelection(start + utf16_length(text) - utf16_length(after))

    def _on_key(self, key_code, event):
        char = CONTROL_KEYS.get(key_code)
        if char is None or not event.isCtrlPressed() or self.input_readonly:
            return False  # read-only, Ctrl-C copies as usual
        if event.getAction() == KeyEvent.ACTION_DOWN and event.getRepeatCount() == 0:
            self.interface._enter_control(char)
        return True

    def _post(self, name, *args):
        self.native.post(TogaCall(self._weak_call(name, *args)))

    def _weak_call(self, name, *args):
        """A call to one of this backend's methods that does not keep it alive."""
        backend = weakref.proxy(self)
        return lambda: getattr(backend, name)(*args)

    @contextmanager
    def _output_edit(self):
        self.editing_output = True
        try:
            yield
        finally:
            self.editing_output = False

    # Selection and styles

    def _drop_caret(self, editable):
        # TextView scrolls the selection into view after every edit, and output
        # appended at a caret carries it along, so in a read-only console a caret
        # would pull the view to itself on every flush, follow or not. A selected
        # range is the user's, and a console taking input keeps its caret.
        if not self.input_readonly:
            return
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
