import pytest
from travertino.colors import Color

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


def test_style_parses_its_colour_on_construction():
    red = Color.parse("red")
    assert Style(color="red").color == red
    assert Style(color=red).color == red
    assert Style().color is None
    with pytest.raises(ValueError, match="no-such-colour"):
        Style(color="no-such-colour")
    # Equality and hashing survive the parse.
    assert Style(color="red") == Style(color="red")
    assert len({Style(color="red"), Style(color="red")}) == 1


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


def test_an_escape_inside_a_csi_starts_a_new_sequence():
    assert make_stream().filter("a\x1b[3\x1b[31mb") == "ab"
    stream = make_stream()
    assert stream.filter("a\x1b[3") == "a"
    assert stream.filter("\x1b[31mb") == "b"


def test_an_escape_inside_an_intermediate_sequence_starts_a_new_sequence():
    assert make_stream().filter("a\x1b(\x1b[31mb") == "ab"


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


def test_reset_returns_a_stream_to_ground():
    stream = make_stream()
    assert stream.filter("\x1b]0;tit") == ""  # an OSC left open
    stream.reset()
    assert stream.filter("x") == "x"
    assert stream.decode(b"\xf0") == ""  # the lead byte of a four-byte character
    stream.reset()
    assert stream.decode(b"ok") == "ok"


from toga_console.model import MAX_LINE_LENGTH, PLAIN, Scrollback  # noqa: E402

RED = Style(color="red")


def test_newline_commits_a_line():
    scrollback = Scrollback(max_lines=10)
    scrollback.put("hello\nwor", PLAIN)
    assert scrollback.text == "hello\nwor"
    assert len(scrollback.lines) == 1


def test_carriage_return_overwrites_from_the_start_of_the_line():
    scrollback = Scrollback(10)
    scrollback.put("Progress: 10%\rProgress: 100%\n", PLAIN)
    assert scrollback.text == "Progress: 100%\n"
    scrollback.put("abcdef\rXY", PLAIN)
    assert scrollback.text == "Progress: 100%\nXYcdef"


def test_crlf_split_across_puts_is_one_line_break():
    scrollback = Scrollback(10)
    scrollback.put("one\r", PLAIN)
    scrollback.put("\ntwo\r\n", PLAIN)
    assert scrollback.text == "one\ntwo\n"


def test_backspace_moves_back_one_column_but_not_past_the_start():
    scrollback = Scrollback(10)
    scrollback.put("abc\b\bX", PLAIN)
    assert scrollback.text == "aXc"
    scrollback.put("\b\b\b\b\bY", PLAIN)
    assert scrollback.text == "YXc"


def test_other_control_characters_are_dropped_and_tabs_kept():
    scrollback = Scrollback(10)
    scrollback.put("a\x00b\x07c\td\x7fe", PLAIN)
    assert scrollback.text == "abc\tde"


def test_lone_surrogates_become_replacement_characters():
    scrollback = Scrollback(10)
    scrollback.put("a\udcffb", PLAIN)
    assert scrollback.text == "a�b"


def test_a_line_at_the_length_limit_is_committed():
    scrollback = Scrollback(10)
    scrollback.put("x" * (MAX_LINE_LENGTH + 1), PLAIN)
    assert len(scrollback.lines) == 1
    assert scrollback.text == "x" * MAX_LINE_LENGTH + "\nx"


def test_oldest_lines_drop_past_max_lines():
    scrollback = Scrollback(2)
    scrollback.put("1\n2\n3\n", PLAIN)
    assert scrollback.text == "2\n3\n"
    scrollback.max_lines = 1
    assert scrollback.text == "3\n"


def test_styles_form_runs_per_line():
    scrollback = Scrollback(10)
    scrollback.put("ab", PLAIN)
    scrollback.put("cd", RED)
    scrollback.put("e\n", RED)
    assert scrollback.lines[0] == [("ab", PLAIN), ("cde", RED)]
    scrollback.put("x", PLAIN)
    scrollback.put("\rY", RED)
    assert scrollback.current_runs() == [("Y", RED)]


def test_text_ends_with_a_newline_only_after_a_newline():
    scrollback = Scrollback(10)
    scrollback.put("a\n", PLAIN)
    assert scrollback.text == "a\n"
    scrollback.put("b", PLAIN)
    assert scrollback.text == "a\nb"


def test_first_flush_replaces_the_tail_with_everything():
    scrollback = Scrollback(10)
    scrollback.put("a\nb", PLAIN)
    flush = scrollback.take_flush()
    assert (flush.clear, flush.trim, flush.runs) == (False, 0, [("a\nb", PLAIN)])
    assert scrollback.take_flush() is None


def test_flush_carries_the_old_tail_new_lines_and_the_new_tail():
    scrollback = Scrollback(10)
    scrollback.put("a", PLAIN)
    scrollback.take_flush()
    scrollback.put("b\nc\nd", RED)
    flush = scrollback.take_flush()
    assert (flush.clear, flush.trim) == (False, 0)
    assert flush.runs == [("a", PLAIN), ("b\nc\nd", RED)]


def test_trim_within_the_displayed_lines():
    scrollback = Scrollback(3)
    scrollback.put("1\n2\n3\n", PLAIN)
    scrollback.take_flush()
    scrollback.put("4\n", PLAIN)
    flush = scrollback.take_flush()
    assert (flush.clear, flush.trim, flush.runs) == (False, 1, [("4\n", PLAIN)])


def test_trim_beyond_the_displayed_lines_clears_and_rebuilds():
    scrollback = Scrollback(3)
    scrollback.put("1\n", PLAIN)
    scrollback.take_flush()
    scrollback.put(
        "2\n3\n4\n5\n", PLAIN
    )  # drops lines 1 and 2: more than was displayed
    flush = scrollback.take_flush()
    assert (flush.clear, flush.trim, flush.runs) == (True, 0, [("3\n4\n5\n", PLAIN)])


def test_a_trim_only_flush_leaves_the_tail_alone():
    scrollback = Scrollback(3)
    scrollback.put("1\n2\n3\n", PLAIN)
    scrollback.take_flush()
    scrollback.max_lines = 2
    flush = scrollback.take_flush()
    assert (flush.clear, flush.trim, flush.runs) == (False, 1, None)


def test_cursor_moves_alone_do_not_dirty_the_tail():
    scrollback = Scrollback(3)
    scrollback.put("abc", PLAIN)
    scrollback.take_flush()
    scrollback.put("\r\b", PLAIN)
    assert scrollback.take_flush() is None


def test_clear_resets_everything():
    scrollback = Scrollback(3)
    scrollback.put("1\n2", PLAIN)
    scrollback.take_flush()
    scrollback.clear()
    assert scrollback.text == ""
    assert scrollback.take_flush() is None
    scrollback.put("x", PLAIN)
    assert scrollback.take_flush().runs == [("x", PLAIN)]
