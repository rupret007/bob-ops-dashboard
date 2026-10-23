"""Keep service state in disposable storage during pytest runs."""
import os
import tempfile
from pathlib import Path


def pytest_configure(config):
    state = tempfile.TemporaryDirectory(prefix="storypool-pytest-")
    config._storypool_test_state = state
    config._storypool_test_environment = {}
    for variable, directory in (
        ("STORYPOOL_DATA_DIR", "budget"),
        ("STORYPOOL_METRICS_DIR", "metrics"),
        ("STORYPOOL_JOBS_DIR", "jobs"),
    ):
        config._storypool_test_environment[variable] = os.environ.get(variable)
        os.environ[variable] = str(Path(state.name) / directory)


def pytest_unconfigure(config):
    for variable, original in config._storypool_test_environment.items():
        if original is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = original
    config._storypool_test_state.cleanup()
