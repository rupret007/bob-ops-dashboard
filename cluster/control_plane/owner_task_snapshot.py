"""Trusted immutable bytes and runner receipts; containment is still mandatory.

No repository-provided result file, stdout, or exit code is execution evidence.
The operator-owned runner must observe individual test call outcomes outside
repository control and bind them to this exact snapshot. No default exists.
"""
from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

from .coordination import CoordinationError


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: tuple

    @classmethod
    def capture(cls, tree):
        tree = Path(tree)
        if tree.is_symlink() or not tree.is_dir():
            raise CoordinationError('Invalid snapshot root')
        files = []
        for path in sorted(tree.rglob('*')):
            relative = path.relative_to(tree).as_posix()
            if relative == '.git':
                continue  # Trusted pointer is checked separately, never copied.
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise CoordinationError('Non-regular file in owner snapshot')
            files.append((relative, bool(mode & 0o111), path.read_bytes()))
        return cls(tuple(files))

    @property
    def digest(self):
        digest = hashlib.sha256()
        for path, executable, content in self.files:
            for field in (path.encode(), b'1' if executable else b'0', content):
                digest.update(str(len(field)).encode() + b':' + field)
        return digest.hexdigest()

    def materialize(self, destination):
        """Publish only trusted bytes, never the mutable test workspace."""
        destination = Path(destination)
        destination.mkdir()  # Require a fresh path; never overwrite host data.
        for relative, executable, content in self.files:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o755 if executable else 0o644)
        return destination


def verify_regression_evidence(evidence, snapshot_digest, required, baseline_sha):
    """Require observed call failures before and passes after for every new test.

    An import/collection error, skip, or suite-wide success is not a regression
    demonstration. This validates a trusted adapter receipt, not its provenance;
    production stays blocked until that observer is implemented and reviewed.
    """
    if (type(evidence) is not dict or evidence.get('snapshot_sha256') != snapshot_digest
            or evidence.get('observer') != 'trusted-outside-sandbox-v1'
            or evidence.get('baseline_sha') != baseline_sha
            or evidence.get('candidate_suite_passed') is not True
            or not required or type(evidence.get('baseline')) is not dict
            or type(evidence.get('candidate')) is not dict
            or any(evidence['baseline'].get(test) != 'failed'
                   or evidence['candidate'].get(test) != 'passed' for test in required)):
        raise CoordinationError('Executed regression evidence missing or unverified')
