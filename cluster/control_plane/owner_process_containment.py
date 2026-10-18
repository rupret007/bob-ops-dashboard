"""Candidate Linux cgroup-v2 lifecycle fence; no production authorization.

The operator must delegate a private domain below /sys/fs/cgroup. The separate
credential/network sandbox MUST hide cgroupfs and prevent the payload moving
itself out. This module does not replace that sandbox or enable owner execution.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import uuid

from .coordination import CoordinationError


class OwnerProcessContainment:
    """Fail closed unless kernel-backed descendant teardown can be confirmed."""

    def __init__(self):
        self.path = None
        if sys.platform != 'linux':
            raise CoordinationError('Owner process containment unsupported on this platform')
        raw = os.environ.get('STORYPOOL_OWNER_CGROUP_ROOT', '')
        root = Path(raw)
        mount = Path('/sys/fs/cgroup')
        if (not raw or not root.is_absolute() or root == mount
                or mount not in root.parents or root.resolve() != root
                or not (mount / 'cgroup.controllers').is_file()):
            raise CoordinationError('Private delegated cgroup-v2 root required')
        path = root / ('webbob-command-' + uuid.uuid4().hex)
        try:
            path.mkdir(mode=0o700)
            self.path = path
            if (path / 'cgroup.type').read_text().strip() != 'domain':
                raise OSError('domain cgroup required')
            # Open now: discover unavailable delegation/cgroup.kill before spawn.
            for name in ('cgroup.procs', 'cgroup.kill'):
                fd = os.open(str(path / name), os.O_WRONLY | os.O_CLOEXEC)
                os.close(fd)
            (path / 'cgroup.events').read_text()
        except OSError:
            if self.path is not None:
                try:
                    self.path.rmdir()
                except OSError:
                    pass
            raise CoordinationError('Owner cgroup containment unavailable; command refused') from None

    def attach_before_exec(self):
        """Child enters its cgroup before it can execute or fork payload code."""
        fd = os.open(str(self.path / 'cgroup.procs'), os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.write(fd, b'0')
        finally:
            os.close(fd)

    def close(self):
        """Kill even detached descendants and wait for populated=0 before return."""
        try:
            (self.path / 'cgroup.kill').write_text('1')
            deadline = time.monotonic() + 5
            while 'populated 0' not in (self.path / 'cgroup.events').read_text().splitlines():
                if time.monotonic() >= deadline:
                    raise OSError('descendant teardown timed out')
                time.sleep(.01)
            self.path.rmdir()
        except OSError:
            raise CoordinationError('Owner descendant teardown unconfirmed; stop all followups') from None
