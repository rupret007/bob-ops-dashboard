"""Keep verification from manufacturing live jobs, budget usage, or PASS metrics."""
import os
import tempfile
from pathlib import Path

_test_state = tempfile.TemporaryDirectory(prefix='storypool-pytest-')
for _name, _relative in {
    'STORYPOOL_DATA_DIR': 'data',
    'STORYPOOL_JOBS_DIR': 'jobs',
    'STORYPOOL_METRICS_DIR': 'metrics',
    'STORYPOOL_FIX_STATE_DIR': 'fixes',
    'STORYPOOL_UPDATE_LOG': 'self-update.log',
}.items():
    os.environ[_name] = str(Path(_test_state.name) / _relative)
