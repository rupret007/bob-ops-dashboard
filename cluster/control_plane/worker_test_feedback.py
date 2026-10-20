"""Additional bounded prechecks; success still requires the original full command."""
import re

from .service_logging import event

MAX_PRECHECK_MODULES = 8


def select_prechecks(files, changed_paths, prior_failed_tests):
    available = sorted(set(files), key=lambda value: value.encode())
    prior = {value.split('::', 1)[0] for value in prior_failed_tests if isinstance(value, str)}
    changed = set()
    for path in changed_paths:
        if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+\.py', path)
                or path.startswith('/') or any(part in ('', '.', '..') for part in path.split('/'))):
            continue
        if path in available:
            changed.add(path)
        basename = 'test_' + path.rsplit('/', 1)[-1]
        changed.update(name for name in available if name.rsplit('/', 1)[-1] == basename)
    selected = [name for name in available if name in prior]
    selected.extend(name for name in available if name in changed and name not in prior)
    return selected[:MAX_PRECHECK_MODULES]


def run_worker_tests(run, full_command, cwd, env, *, runtime=None, files=(),
                     changed_paths=(), prior_failed_tests=()):
    if runtime is not None:
        selected = select_prechecks(files, changed_paths, prior_failed_tests)
        event('fix-worker', 'test_precheck_plan', precheck_modules=len(selected),
              full_modules=len(files), original_full_suite_required=True)
        # Do not duplicate a precheck that already covers the entire test plan.
        if selected and set(selected) != set(files):
            run(runtime.test_command(selected), cwd, env=env)
    return run(full_command, cwd, env=env)
