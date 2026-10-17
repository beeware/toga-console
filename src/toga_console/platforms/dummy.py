from toga_dummy.widgets.multilinetextinput import MultilineTextInput


class Console(MultilineTextInput):
    """Records the backend calls and mirrors them on a shadow string.

    The shadow lets the tests check that the calls the widget issues reproduce the
    model's text, which is the contract every native backend meets.
    """

    def create(self):
        self._action("create Console")
        self.shadow = ""

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
