import asyncio
import random
import warnings

import pytest
from toga.fonts import MONOSPACE, SERIF
from toga.platform import get_factory
from toga.style import Pack
from toga_dummy.utils import EventLog, assert_action_performed, attribute_value

from toga_console import Console, Style
from toga_console.console import FLUSH_INTERVAL

RED = Style(color="red")


def actions(widget, name):
    """The recorded calls of one backend action, oldest first; [] when none."""
    try:
        return EventLog.performed_actions(widget, name)
    except AttributeError:
        return []


def test_defaults(app):
    console = Console()

    assert_action_performed(console, "create Console")
    assert console.value == ""
    assert console.max_lines == 2000
    assert console.follow is True
    assert console.readonly is True
    assert console.style.font_family == [MONOSPACE]
    assert console.styles == {"stderr": RED}
    assert attribute_value(console, "readonly") is True
    assert actions(console, "replace tail") == []
    assert actions(console, "clear") == []


def test_readonly_cannot_be_turned_off(app):
    console = Console()
    assert console.readonly is True
    with pytest.raises(ValueError, match="always read-only"):
        console.readonly = False
    assert attribute_value(console, "readonly") is True


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"font_family": SERIF}, [SERIF]),
        ({"style": Pack(font_family=SERIF)}, [SERIF]),
        ({"style": Pack(flex=1)}, [MONOSPACE]),
    ],
)
def test_font_family(app, kwargs, expected):
    """Monospace is the default unless the caller chose a font family."""
    assert Console(**kwargs).style.font_family == expected


def test_placeholder_passes_through(app):
    assert (
        attribute_value(Console(placeholder="Nothing yet"), "placeholder")
        == "Nothing yet"
    )


def test_constructor_validation(app):
    with pytest.raises(ValueError, match="max_lines"):
        Console(max_lines=0)
    with pytest.raises(LookupError):
        Console(encoding="no-such-codec")
    with pytest.raises(LookupError, match="bogus"):
        Console(errors="bogus")


def test_no_interface_warning(app):
    get_factory.cache_clear()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Console()


async def test_write_shows_after_a_flush(app):
    console = Console()
    console.write(b"hello\n")
    assert console.value == "hello\n"
    assert console._impl.shadow == ""
    console.flush()
    assert console._impl.shadow == "hello\n"
    assert console._impl.get_value() == "hello\n"
    assert actions(console, "replace tail") == [
        {"action": "replace tail", "runs": [("hello\n", Style())]}
    ]


def test_write_without_a_running_loop_shows_at_once(app):
    console = Console()
    console.write("now")
    assert console._impl.shadow == "now"


async def test_writes_coalesce_into_one_flush(app):
    console = Console()
    console.write("a")
    console.write("b")
    assert actions(console, "replace tail") == []
    await asyncio.sleep(FLUSH_INTERVAL * 3)
    assert [call["runs"] for call in actions(console, "replace tail")] == [
        [("ab", Style())]
    ]
    assert console._impl.shadow == "ab"


async def test_flush_now_cancels_the_pending_flush(app):
    console = Console()
    console.write("a")
    console.flush()
    await asyncio.sleep(FLUSH_INTERVAL * 3)
    assert len(actions(console, "replace tail")) == 1


async def test_a_flush_task_cancelled_from_outside_does_not_block_later_flushes(app):
    console = Console()
    console.write("a")
    console._pending_flush.cancel()  # as an app cancelling its tasks might
    await asyncio.sleep(0)
    assert console._pending_flush.done()
    console.write("b")
    await asyncio.sleep(FLUSH_INTERVAL * 3)
    assert console._impl.shadow == "ab"


async def test_on_change_fires_once_per_changed_flush(app):
    changes = []
    console = Console(on_change=lambda widget, **kwargs: changes.append(widget.value))
    console.write("a")
    console.write("b")
    await asyncio.sleep(FLUSH_INTERVAL * 3)
    assert changes == ["ab"]
    console.write("\r\b")  # cursor moves only: the display does not change
    console.flush()
    assert changes == ["ab"]
    console.flush()  # nothing pending
    assert changes == ["ab"]


def test_streams_keep_their_own_decoder_and_style(app):
    console = Console()
    snake = "🐍".encode()
    console.write(snake[:2], stream="stderr")
    console.write(b"out", stream="stdout")
    console.write(snake[2:] + b"\n", stream="stderr")
    assert console.value == "out🐍\n"
    assert actions(console, "replace tail")[-1]["runs"] == [
        ("out", Style()),
        ("🐍\n", RED),
    ]


def test_styles_apply_to_later_writes(app):
    console = Console(styles={"log": Style(bold=True)})
    console.write("a", stream="log")
    console.styles["log"] = Style(italic=True)
    console.write("b", stream="log")
    assert actions(console, "replace tail")[-1]["runs"] == [
        ("a", Style(bold=True)),
        ("b", Style(italic=True)),
    ]


def test_write_rejects_other_types(app):
    with pytest.raises(TypeError, match="bytes or str"):
        Console().write(42)


def test_escape_sequences_and_carriage_returns(app):
    console = Console()
    console.write(
        b"\x1b]0;status\x1b\\Progress: 1%\rProgress: 100%\r\n\x1b[31mred\x1b[0m\n"
    )
    assert console.value == "Progress: 100%\nred\n"
    assert console._impl.shadow == console.value


def test_lone_surrogates_never_reach_the_display(app):
    # surrogateescape decodes the undecodable byte 0xE9 to the lone surrogate U+DCE9.
    console = Console(errors="surrogateescape")
    console.write(b"caf\xe9\n")
    assert console.value == "caf�\n"
    assert console._impl.shadow == "caf�\n"


def test_value_assignment_clears_then_writes(app):
    console = Console()
    console.write("old")
    console.value = "new\n"
    assert console.value == "new\n"
    assert console._impl.shadow == "new\n"
    assert len(actions(console, "clear")) == 1
    console.value = None
    assert console.value == ""
    assert console._impl.shadow == ""


def test_clear(app):
    console = Console()
    console.write("a\nb")
    console.clear()
    assert console.value == ""
    assert console._impl.shadow == ""
    console.write("c")
    assert console._impl.shadow == "c"


def test_max_lines_trims_at_once(app):
    console = Console(max_lines=3)
    console.write("1\n2\n3\n4\n")
    assert console._impl.shadow == "2\n3\n4\n"
    console.max_lines = 1
    assert console.value == "4\n"
    assert console._impl.shadow == "4\n"
    assert actions(console, "trim lines")[-1]["count"] == 2
    with pytest.raises(ValueError, match="max_lines"):
        console.max_lines = 0


def test_a_burst_larger_than_max_lines_rebuilds_the_display(app):
    console = Console(max_lines=5)
    console.write("seed\n")
    console.write("".join(f"{i}\n" for i in range(50)))
    assert console._impl.shadow == "45\n46\n47\n48\n49\n"
    assert len(actions(console, "clear")) == 1
    assert actions(console, "trim lines") == []


def test_follow_scrolls_after_each_changed_flush(app):
    console = Console()
    console.write("a")
    assert len(actions(console, "scroll to bottom")) == 1
    console.follow = False
    console.write("b")
    assert len(actions(console, "scroll to bottom")) == 1
    console.follow = True  # turning it on scrolls at once
    assert len(actions(console, "scroll to bottom")) == 2


def test_random_writes_keep_the_display_equal_to_the_model(app):
    rng = random.Random(20261007)
    pieces = [
        "a",
        "b",
        "é",
        "🐍",
        "\n",
        "\r",
        "\b",
        "\t",
        "\x1b[31m",
        "\x1b]0;t\x07",
        "x\ny\nz\n",
        "\x00",
        "Progress: 50%\r",
    ]
    console = Console(max_lines=4)
    for step in range(600):
        roll = rng.random()
        if roll < 0.8:
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 6)))
            stream = rng.choice(["stdout", "stderr"])
            console.write(text.encode() if rng.random() < 0.5 else text, stream=stream)
        elif roll < 0.9:
            console.max_lines = rng.randint(1, 6)
        else:
            console.clear()
        assert console._impl.shadow == console.value, f"step {step}"
