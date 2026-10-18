"""Run one read-only watchdog check in an isolated, bounded process group."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CHECK_TIMEOUT = 20
CHECKS = {
    'check_service_stuck', 'check_tracebacks', 'check_gh_in_path',
    'check_codex_in_path', 'check_labels_exist', 'check_install_stale', 'check_head_lag',
}


def bounded_output(argv, timeout, **kwargs):
    """Timeout kills descendants too; no abandoned git/gh process after a check."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, **kwargs)
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, argv)
        return stdout
    finally:
        # Also clean up descendants of a child which exited early.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def run_check(function, *args):
    from .watchdog import HealthCheck, logger
    name = function.__name__
    if name not in CHECKS:
        raise ValueError('Unknown watchdog check')
    check_name = (f'{args[0]}_stuck' if name == 'check_service_stuck' else
                  f'{args[0]}_tracebacks' if name == 'check_tracebacks' else
                  'labels_' + args[0].replace('/', '_') if name == 'check_labels_exist' else
                  {'check_gh_in_path': 'gh_available', 'check_codex_in_path': 'codex_available',
                   'check_install_stale': 'install_stale', 'check_head_lag': 'head_lag'}[name])
    started = time.monotonic()
    try:
        output = bounded_output(
            [sys.executable, '-m', 'cluster.control_plane.watchdog_check', name,
             json.dumps([str(arg) for arg in args])], CHECK_TIMEOUT)
        result = HealthCheck(**json.loads(output))
    except Exception as exc:
        # Neither captured output nor exception text is safe to copy to logs.
        kind = 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else type(exc).__name__
        result = HealthCheck(check_name, False, f'Check execution failed: {kind}',
                             can_repair=False, execution_error=True)
    logger.info('check=%s duration=%.3fs passed=%s', check_name, time.monotonic() - started,
                result.passed)
    return result


def main():
    from . import watchdog
    name, arguments = sys.argv[1:]
    if name not in CHECKS:
        raise ValueError('Unknown watchdog check')
    args = json.loads(arguments)
    if name in ('check_service_stuck', 'check_tracebacks'):
        args[1] = Path(args[1])
    result = getattr(watchdog, name)(*args)
    print(json.dumps(result.to_dict()))


if __name__ == '__main__':
    main()
