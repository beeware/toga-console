from toga_gtk.libs import GTK_VERSION, Gdk, Gtk, Pango
from toga_gtk.widgets.multilinetextinput import MultilineTextInput


class Console(MultilineTextInput):
    def create(self):
        if GTK_VERSION >= (4, 0, 0):
            # toga-gtk's GTK 4 MultilineTextInput builds no text view yet.
            raise RuntimeError("the Console needs GTK 3; unset TOGA_GTK=4")
        super().create()
        # A mark with right gravity stays at the end of the buffer, so following the
        # output never moves the cursor or the user's selection.
        self.end_mark = self.buffer.create_mark(None, self.buffer.get_end_iter(), False)
        self.tags = {}

    # Inherited methods

    def set_readonly(self, value):
        # Never editable; the text stays selectable.
        self.native_textview.set_property("editable", False)
        self.native_textview.set_property("cursor-visible", False)

    def gtk_on_changed(self, *args):
        # The buffer changes for every edit below; the interface reports its own
        # on_change once per flush.
        pass

    def scroll_to_bottom(self):
        # The mark belongs to the content buffer, and the view can only scroll to a
        # mark in the buffer it shows; while the placeholder shows, there is nothing
        # to scroll. GTK defers the scroll until the new text is laid out.
        if self.native_textview.get_buffer() is self.buffer:
            self.native_textview.scroll_to_mark(self.end_mark, 0.0, True, 0.0, 1.0)

    # Console backend contract

    def replace_tail(self, runs):
        buffer = self.buffer
        start = buffer.get_end_iter()
        found = start.backward_search("\n", Gtk.TextSearchFlags.TEXT_ONLY, None)
        start = buffer.get_start_iter() if found is None else found[1]
        buffer.delete(start, buffer.get_end_iter())
        for text, style in runs:
            buffer.insert_with_tags(buffer.get_end_iter(), text, self._tag(style))
        self._update_placeholder()

    def trim_lines(self, count):
        buffer = self.buffer
        end = buffer.get_start_iter()
        for _ in range(count):
            found = end.forward_search("\n", Gtk.TextSearchFlags.TEXT_ONLY, None)
            if found is None:
                end = buffer.get_end_iter()
                break
            end = found[1]
        buffer.delete(buffer.get_start_iter(), end)
        self._update_placeholder()

    def clear(self):
        self.buffer.set_text("")
        self._update_placeholder()

    # Styles and placeholder

    def _update_placeholder(self):
        # The base swaps in its placeholder buffer from set_value, which the console
        # never calls, so the edits above choose the buffer instead. Focus and key
        # presses still swap as they do in the base.
        shown = self.get_value() or self.has_focus
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
