import pytest
from toga.keys import Key
from toga_dummy.utils import EventLog

from toga_console import Console, Style
from toga_console.console import CONTROL


def recording_console(**kwargs):
    """A writable console, and the list each on_input call's bytes are added to."""
    received = []
    console = Console(
        readonly=False,
        on_input=lambda widget, data, **kw: received.append((widget, data)),
        **kwargs,
    )
    return console, received


def test_typing_into_a_readonly_console_does_nothing(app):
    received = []
    console = Console(on_input=lambda widget, data, **kw: received.append(data))
    console.type("1 + 1\n")
    console.press(CONTROL + Key.C)
    console.press(Key.BACKSPACE)
    assert console._impl.input == ""
    assert console.value == ""
    assert received == []


def test_a_line_is_echoed_on_stdin_and_shown_at_once(app):
    console, received = recording_console()
    console.write(">>> ")
    console.flush()
    console.type("1 + ")
    assert console._impl.input == "1 + "
    assert console.value == ">>> "
    assert received == []
    console.type("1\n")
    assert console._impl.input == ""
    # The echo joins the prompt's line, and the display does not wait for the flush.
    assert console.value == ">>> 1 + 1\n"
    assert console._impl.shadow == ">>> 1 + 1\n"
    assert console._pending_flush is None
    assert received == [(console, b"1 + 1\n")]


def test_a_paste_enters_its_complete_lines_at_once(app):
    console, received = recording_console()
    console.type("one\r\ntwo\rthree\nfour")
    assert console.value == "one\ntwo\nthree\n"
    assert console._impl.input == "four"
    assert received == [(console, b"one\ntwo\nthree\n")]


def test_an_empty_line_is_entered(app):
    console, received = recording_console()
    console.type("\n")
    assert received == [(console, b"\n")]


def test_the_stdin_style_applies_to_the_echo(app):
    bold = Style(bold=True)
    console, _received = recording_console(styles={"stdin": bold})
    console.write("$ ")
    console.type("x\n")
    runs = EventLog.performed_actions(console, "replace tail")[-1]["runs"]
    assert runs == [("$ ", Style()), ("x\n", bold)]


def test_input_uses_the_console_encoding(app):
    console, received = recording_console(encoding="latin-1")
    console.type("é€\n")
    assert received == [(console, b"\xe9?\n")]


@pytest.mark.parametrize(("key", "byte"), [(Key.C, b"\x03"), (Key.D, b"\x04")])
def test_control_keys_send_one_byte_and_keep_the_input(app, key, byte):
    console, received = recording_console()
    console.type("half")
    console.press(CONTROL + key)
    assert received == [(console, byte)]
    assert console._impl.input == "half"
    assert console.value == ""  # no echo: the handler decides what the key means


def test_only_ctrl_c_and_ctrl_d_are_control_keys(app):
    console, _received = recording_console()
    with pytest.raises(ValueError, match="control key"):
        console._enter_control("\x1a")


def test_input_without_a_handler_is_only_echoed(app):
    console = Console(readonly=False)
    console.type("x\n")
    console.press(CONTROL + Key.D)
    assert console.value == "x\n"


def test_on_input_can_be_replaced(app):
    console, received = recording_console()
    later = []
    console.on_input = lambda widget, data, **kw: later.append(data)
    console.type("x\n")
    assert received == []
    assert later == [b"x\n"]


def test_text_without_a_line_break_stays_in_the_input(app):
    console, received = recording_console()
    assert console._enter_input("abc") == "abc"
    assert received == []
    assert console.value == ""


def test_press_enter_and_backspace(app):
    console, received = recording_console()
    console.type("ab")
    console.press(Key.BACKSPACE)
    console.press(Key.ENTER)
    assert received == [(console, b"a\n")]


def test_press_rejects_other_keys(app):
    console, _received = recording_console()
    with pytest.raises(ValueError, match="cannot press"):
        console.press(Key.TAB)


def test_text_and_caret(app):
    console, _received = recording_console()
    console.write("> ")
    console.type("x")
    assert console.text() == "> x"
    console.caret_to(2)
    assert EventLog.performed_actions(console, "caret to")[-1]["index"] == 2
