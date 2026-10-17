from toga_dummy.widgets.multilinetextinput import MultilineTextInput


class Console(MultilineTextInput):
    """Records the backend calls and mirrors them on a shadow string.

    The shadow lets the tests check that the calls the widget issues reproduce the
    model's text, which is the contract every native backend meets. ``input`` stands
    for the text typed after the output, which the output calls never touch.
    """

    def create(self):
        self._action("create Console")
        self.shadow = ""
        self.input = ""

    def get_value(self):
        return self.shadow

    # Console backend contract

    def replace_tail(self, runs):
        self._action("replace tail", runs=runs)
        head, newline, _tail = self.shadow.rpartition("\n")
        kept = head + newline if newline else ""
        self.shadow = kept + "".join(text for text, _style in runs)

    def trim_lines(self, count):
        self._action("trim lines", count=count)
        for _ in range(count):
            _line, _newline, self.shadow = self.shadow.partition("\n")

    def clear(self):
        self._action("clear")
        self.shadow = ""

    # Typing, as a native backend sees it

    def type(self, text):
        """Type ``text`` at the end of the input, as a user would."""
        self._action("type", text=text)
        if self.get_readonly():
            return
        text = self.input + text
        if "\n" in text or "\r" in text:
            text = self.interface._enter_input(text)
        self.input = text
