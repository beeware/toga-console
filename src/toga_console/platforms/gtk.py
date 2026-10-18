import weakref
from contextlib import contextmanager, suppress

from toga.handlers import WeakrefCallable
from toga_gtk.libs import GTK_VERSION, Gdk, GLib, Gtk, Pango
from toga_gtk.widgets.multilinetextinput import MultilineTextInput

CHORD_MODIFIERS = (
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.MOD1_MASK
    | Gdk.ModifierType.SUPER_MASK
    | Gdk.ModifierType.META_MASK
)
# The console's control keys, by the key pressed with Control.
CONTROL_KEYS = {Gdk.KEY_c: "\x03", Gdk.KEY_d: "\x04"}


class Console(MultilineTextInput):
    """The text is the output, then the input area: what the user is typing.

    The ``output_end`` mark is where the input area starts. The output calls edit
    only the text before it, and the buffer's insert-text and delete-range handlers
    let the user edit only the text after it. Those handlers see every edit, so the
    console's own edits run with ``editing`` set, which they let through.
    """

    def create(self):
        if GTK_VERSION >= (4, 0, 0):
            # toga-gtk's GTK 4 MultilineTextInput builds no text view yet.
            raise RuntimeError("the Console needs GTK 3; unset TOGA_GTK=4")
        super().create()
        buffer = self.buffer
        # A mark with right gravity stays at the end of the buffer, so following the
        # output never moves the cursor or the user's selection.
        self.end_mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        # With left gravity, text typed where the input starts goes after the mark.
        self.output_end = buffer.create_mark(None, buffer.get_end_iter(), True)
        # With right gravity, each run inserted here moves the mark past itself.
        self.insert_point = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.tags = {}
        self.input_readonly = True
        self.editing = False
        buffer.connect("insert-text", WeakrefCallable(self.gtk_insert_text))
        buffer.connect("delete-range", WeakrefCallable(self.gtk_delete_range))

    @property
    def output_length(self):
        """Where the input area starts, as a character offset."""
        return self._output_iter().get_offset()

    # Inherited methods

    def get_readonly(self):
        return self.input_readonly

    def set_readonly(self, value):
        self.input_readonly = bool(value)
        self.native_textview.set_property("editable", not self.input_readonly)
        self.native_textview.set_property("cursor-visible", not self.input_readonly)

    def get_value(self):
        return self.buffer.get_text(
            self.buffer.get_start_iter(), self._output_iter(), True
        )

    def gtk_on_changed(self, *args):
        # The buffer changes for every edit below; the interface reports its own
        # on_change once per flush.
        pass

    def gtk_on_focus_out(self, *args):
        # The base would show the placeholder whenever there is no output, even over
        # a line being typed.
        self._update_placeholder()
        return False

    def gtk_on_key_press(self, widget, event):
        # The base makes sure the content buffer shows before a key types into it.
        self.native_textview.set_buffer(self.buffer)
        if not self.input_readonly and event.state & CHORD_MODIFIERS == (
            Gdk.ModifierType.CONTROL_MASK
        ):
            char = CONTROL_KEYS.get(Gdk.keyval_to_lower(event.keyval))
            if char is not None:
                self.interface._enter_control(char)
                return True
        return False  # read-only, Ctrl-C copies as usual

    def scroll_to_bottom(self):
        # The mark belongs to the content buffer, and the view can only scroll to a
        # mark in the buffer it shows; while the placeholder shows, there is nothing
        # to scroll. GTK defers the scroll until the new text is laid out.
        if self.native_textview.get_buffer() is self.buffer:
            self.native_textview.scroll_to_mark(self.end_mark, 0.0, True, 0.0, 1.0)

    # Console backend contract

    def replace_tail(self, runs):
        buffer = self.buffer
        with self._editing():
            end = self._output_iter()
            found = end.backward_search("\n", Gtk.TextSearchFlags.TEXT_ONLY, None)
            start = buffer.get_start_iter() if found is None else found[1]
            buffer.delete(start, end)
            buffer.move_mark(self.insert_point, start)
            # The user's cursor, the insert mark, has right gravity too, so a cursor
            # at the start of the input stays there.
            for text, style in runs:
                buffer.insert_with_tags(
                    buffer.get_iter_at_mark(self.insert_point), text, self._tag(style)
                )
            buffer.move_mark(
                self.output_end, buffer.get_iter_at_mark(self.insert_point)
            )
        self._update_placeholder()

    def trim_lines(self, count):
        buffer = self.buffer
        with self._editing():
            limit = self._output_iter()
            end = buffer.get_start_iter()
            for _ in range(count):
                found = end.forward_search("\n", Gtk.TextSearchFlags.TEXT_ONLY, limit)
                if found is None:
                    end = limit
                    break
                end = found[1]
            buffer.delete(buffer.get_start_iter(), end)
        self._update_placeholder()

    def clear(self):
        with self._editing():
            self.buffer.delete(self.buffer.get_start_iter(), self._output_iter())
        self._update_placeholder()

    # Typing

    def gtk_insert_text(self, buffer, location, text, length):
        """Let typing into the input area through; take over the rest."""
        if self.editing:
            return
        start = self.output_length
        offset = location.get_offset()
        if offset >= start and "\n" not in text and "\r" not in text:
            return
        buffer.stop_emission_by_name("insert-text")
        # The iterators must not outlive this handler, and the buffer must not change
        # inside it, so the edit is made once the main loop is idle.
        if offset < start:
            # Typed with the cursor in the output: it belongs at the end of the input.
            before, after = self._input_text(), ""
        else:
            before = buffer.get_text(self._output_iter(), location, True)
            after = buffer.get_text(location, buffer.get_end_iter(), True)
        GLib.idle_add(self._weak_call("_apply_input", before + text + after, after))

    def gtk_delete_range(self, buffer, start, end):
        if not self.editing and start.get_offset() < self.output_length:
            buffer.stop_emission_by_name("delete-range")  # it reaches the output

    def _apply_input(self, text, after):
        if "\n" in text or "\r" in text:
            text = self.interface._enter_input(text)
        buffer = self.buffer
        with self._editing():
            buffer.delete(self._output_iter(), buffer.get_end_iter())
            buffer.insert(self._output_iter(), text)
        caret = self.output_length + len(text) - len(after)
        buffer.place_cursor(buffer.get_iter_at_offset(caret))
        self.native_textview.scroll_mark_onscreen(buffer.get_insert())

    def _input_text(self):
        return self.buffer.get_text(
            self._output_iter(), self.buffer.get_end_iter(), True
        )

    def _output_iter(self):
        return self.buffer.get_iter_at_mark(self.output_end)

    @contextmanager
    def _editing(self):
        self.editing = True
        try:
            yield
        finally:
            self.editing = False

    def _weak_call(self, name, *args):
        """An idle callback to one of this backend's methods; it runs once."""
        backend = weakref.proxy(self)

        def call():
            with suppress(ReferenceError):
                getattr(backend, name)(*args)
            return GLib.SOURCE_REMOVE

        return call

    # Styles and placeholder

    def _update_placeholder(self):
        # The base swaps in its placeholder buffer from set_value, which the console
        # never calls, so the edits above choose the buffer instead. Focus and key
        # presses still swap as they do in the base.
        shown = self.buffer.get_char_count() or self.has_focus
        buffer = self.buffer if shown else self.placeholder
        if self.native_textview.get_buffer() is not buffer:
            self.native_textview.set_buffer(buffer)

    def _tag(self, style):
        try:
            return self.tags[style]
        except KeyError:
            pass
        # A tag only sets what the style changes; the rest comes from the widget's
        # CSS, so a later font or colour change still reaches styled text.
        properties = {}
        if style.color is not None:
            color = style.color.rgb
            properties["foreground_rgba"] = Gdk.RGBA(
                color.r / 255, color.g / 255, color.b / 255, color.a
            )
        if style.bold:
            properties["weight"] = Pango.Weight.BOLD
        if style.italic:
            properties["style"] = Pango.Style.ITALIC
        tag = self.tags[style] = self.buffer.create_tag(None, **properties)
        return tag
