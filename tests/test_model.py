import pytest

from toga_console.model import MAX_SEQUENCE_LENGTH, Stream, Style


def make_stream(encoding="utf-8", errors="replace"):
    return Stream("stdout", encoding, errors)


def test_style_is_a_frozen_value():
    style = Style()
    assert (style.color, style.bold, style.italic) == (None, False, False)
    assert Style(color="red") == Style(color="red")
    assert {Style(bold=True)} == {Style(bold=True)}
    with pytest.raises(AttributeError):
        style.bold = True


def test_decodes_a_multibyte_character_split_across_chunks():
    stream = make_stream()
    snake = "🐍".encode()
    assert stream.decode(snake[:2]) == ""
    assert stream.decode(snake[2:]) == "🐍"


def test_undecodable_bytes_follow_the_errors_policy():
    # A trailing 0xE9 would be held back as the start of a character split across
    # chunks. The "s" after it shows that the byte is undecodable.
    assert make_stream().decode(b"caf\xe9s") == "caf�s"
    assert make_stream(encoding="latin-1").decode(b"caf\xe9s") == "cafés"


@pytest.mark.parametrize(
    "sequence",
    [
        "\x1b[31m",  # SGR colour
        "\x1b[0K",  # erase to end of line
        "\x1b[2J",  # clear screen
        "\x1b[?25l",  # hide cursor, private parameter
        "\x1b]0;Done | 10.3.1\x07",  # OSC title, BEL terminator
        "\x1b]0;Done | 10.3.1\x1b\\",  # OSC title, ST terminator
        "\x1b(B",  # charset designation, intermediate + final
        "\x1b7",  # save cursor
        "\x1bM",  # reverse index
        "\x1b=",  # keypad mode
    ],
)
def test_escape_sequences_are_stripped(sequence):
    assert make_stream().filter(f"a{sequence}b") == "ab"


def test_an_escape_sequence_split_across_chunks():
    stream = make_stream()
    assert stream.filter("a\x1b]0;tit") == "a"
    assert stream.filter("le\x1b") == ""
    assert stream.filter("\\b") == "b"


def test_a_csi_sequence_split_across_chunks():
    stream = make_stream()
    assert stream.filter("x\x1b[3") == "x"
    assert stream.filter("1my") == "y"


def test_an_escape_inside_an_osc_starts_a_new_sequence():
    # ESC that is not followed by a backslash abandons the OSC and opens a new sequence.
    assert make_stream().filter("a\x1b]0;title\x1b[31mb") == "ab"


def test_an_escape_followed_by_a_control_character_is_text():
    assert make_stream().filter("a\x1b\nb") == "a\nb"


def test_an_endless_sequence_is_abandoned_at_the_bound():
    stream = make_stream()
    assert stream.filter("\x1b]" + "x" * (MAX_SEQUENCE_LENGTH - 2)) == ""
    assert stream.filter("after") == "after"
    assert stream.filter("\x1b]" + "x" * (MAX_SEQUENCE_LENGTH - 2) + "tail") == "tail"


def test_streams_keep_their_own_parser_state():
    one, two = make_stream(), make_stream()
    assert one.filter("\x1b[") == ""
    assert two.filter("plain") == "plain"
    assert one.filter("mdone") == "done"
