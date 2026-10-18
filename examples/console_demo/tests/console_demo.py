"""The test entry point Briefcase runs for ``briefcase dev --test``."""

import os
import sys
from pathlib import Path

import pytest


def run_tests(app_module):
    # The self-test starts this module's app; see test_selftest.py.
    os.environ["CONSOLE_DEMO_MODULE"] = app_module
    os.chdir(Path(__file__).parent.parent)
    returncode = pytest.main(
        [
            "--no-header",
            "-vv",
            # Write no cache into the project.
            "-p",
            "no:cacheprovider",
            *sys.argv[1:],
        ]
    )
    # Briefcase reads the suite's result from this line.
    print(f">>>>>>>>>> EXIT {returncode} <<<<<<<<<<", flush=True)  # noqa: T201


if __name__ == "__main__":
    run_tests("console_demo")
