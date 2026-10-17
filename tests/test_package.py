from importlib.metadata import version

import toga_console


def test_version():
    assert toga_console.__version__ == version("toga-console")
