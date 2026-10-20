"""Explicit owner release holds are independent of CI and Karen approval."""
from __future__ import annotations

import json
import re
import subprocess

from . import github_reads


_TITLE = re.compile(r'^\s*(?:\[\s*(?:held|hold|do[ -]+not[ -]+merge)\s*\]|'
                    r'(?:held|hold)\s*:|do[ -]+not[ -]+merge\b)', re.IGNORECASE)
_LABELS = frozenset(('held', 'hold', 'release-hold', 'do-not-merge'))


def explicitly_held(pr):
    """Recognize explicit markers; current release admission validates shape."""
    if not isinstance(pr, dict):
        return False
    title = pr.get('title')
    if isinstance(title, str) and _TITLE.search(title):
        return True
    labels = pr.get('labels')
    return isinstance(labels, list) and any(
        isinstance(label, dict) and isinstance(label.get('name'), str)
        and label['name'].strip().casefold().replace('_', '-').replace(' ', '-') in _LABELS
        for label in labels)


def guard_current_release(repo, number, sha):
    """A fresh exact-head response must explicitly authorize an unheld release."""
    if (not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or type(number) is not int or number < 1
            or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)):
        raise PermissionError('Invalid release identity')
    try:
        result = github_reads.run(['pr', 'view', str(number), '--repo', repo, '--json',
                                   'number,state,isDraft,headRefOid,title,labels'], timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise PermissionError('Current release metadata unavailable') from None
    if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > 65536:
        raise PermissionError('Current release metadata unavailable')
    try:
        pr = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise PermissionError('Current release metadata invalid') from None
    if (not isinstance(pr, dict) or type(pr.get('number')) is not int
            or pr['number'] != number or pr.get('state') != 'OPEN'
            or pr.get('isDraft') is not False or pr.get('headRefOid') != sha
            or not isinstance(pr.get('title'), str) or not isinstance(pr.get('labels'), list)
            or not all(isinstance(label, dict) and isinstance(label.get('name'), str)
                       for label in pr['labels'])):
        raise PermissionError('Current release metadata cannot authorize this head')
    if explicitly_held(pr):
        raise PermissionError('PR has an explicit release hold')
