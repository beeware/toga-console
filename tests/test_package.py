from importlib.metadata import entry_points, version
from importlib.util import find_spec

import toga_console


def test_version():
    assert toga_console.__version__ == version("toga-console")


def test_every_backend_entry_point_names_a_module():
    # Found without importing the module, so the native backends are checked too.
    backends = {
        group: entry_point
        for group in entry_points().groups
        if group.startswith("toga_console.backend.")
        for entry_point in entry_points(group=group)
    }
    assert set(backends) >= {
        f"toga_console.backend.{backend}"
        for backend in (
            "toga_android",
            "toga_cocoa",
            "toga_dummy",
            "toga_gtk",
            "toga_iOS",
            "toga_qt",
        )
    }
    for entry_point in backends.values():
        assert entry_point.name == "Console"
        assert find_spec(entry_point.module) is not None, entry_point.value
