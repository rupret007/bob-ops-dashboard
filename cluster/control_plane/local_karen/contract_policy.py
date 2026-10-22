"""Validate the frozen packet using the code-owned contract_record authority."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .runtime_status import deployment_status
from .contract_record import (Contract, VerifiedContract, ContractUnavailable, APPROVED,
                              BASE_ADVANCEMENTS, ADMITTED_BASE, POLICY_FILES, _SHA, _DIFF_ARGS)


_ROOT = Path(__file__).resolve().parents[3]


def _snapshot_policy(module_root):


    try:
        return {p: (module_root / p).read_bytes() for p in POLICY_FILES}
    except OSError:
        return {}


_LOADED_FILES = _snapshot_policy(_ROOT)


def _startup_base():
    try:
        runtime = deployment_status()
        if not isinstance(runtime, dict): return None
        head = runtime.get('loaded_head')
        return head if (runtime.get('source_matches') is True
                        and isinstance(head, str) and _SHA.fullmatch(head)) else None
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


_UNSET = object()
_LOADED_BASE = _UNSET


def bind_startup_base():
    global _LOADED_BASE
    if _LOADED_BASE is _UNSET: _LOADED_BASE = _startup_base()


def is_candidate(owner, repo, number, head):
    return (owner, repo, number, head) == (
        APPROVED.owner, APPROVED.repo, APPROVED.number, APPROVED.head)


def runtime_base_matches(base):
    runtime = deployment_status()
    return (isinstance(base, str) and _SHA.fullmatch(base) is not None
            and base == _LOADED_BASE and set(_LOADED_FILES) == POLICY_FILES
            and runtime.get('source_matches') is True
            and runtime.get('installed_head') == runtime.get('loaded_head') == base)


def _require(condition):
    if not condition:
        raise ContractUnavailable('Exact test-contract provenance not established')


def _assertion_line(source, text):
    """Require the unique exact assertion directly in the named test body."""
    lines = source.decode('utf-8').splitlines()
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == APPROVED.function]
    _require(len(functions) == 1 and lines.count(text) == 1)
    assertions = [n for n in functions[0].body if isinstance(n, ast.Assert)
                  and n.lineno == n.end_lineno and lines[n.lineno - 1] == text]
    _require(len(assertions) == 1)
    return assertions[0].lineno


def _prove(base, git):
    """git reads a freshly fetched bare object store; it never checks out code."""
    _require(isinstance(base, str) and _SHA.fullmatch(base) is not None)
    _require(base == _LOADED_BASE)
    runtime = deployment_status()
    _require(runtime.get('source_matches') is True
             and runtime.get('installed_head') == runtime.get('loaded_head') == base)
    _require(set(_LOADED_FILES) == POLICY_FILES)
    _require(git('merge-base', base, APPROVED.head).decode().strip() == APPROVED.baseline)
    previous = APPROVED.baseline
    _require(type(BASE_ADVANCEMENTS) is tuple and bool(BASE_ADVANCEMENTS))
    for commit, parent in BASE_ADVANCEMENTS:
        _require(parent == previous and isinstance(commit, str) and _SHA.fullmatch(commit) is not None)
        _require(git('rev-list', '--parents', '-n', '1', commit).decode().split() == [commit, parent])
        previous = commit
    _require(previous == ADMITTED_BASE)
    _require(git('merge-base', ADMITTED_BASE, base).decode().strip() == ADMITTED_BASE)
    changed_base = git('diff', '--name-only', '--no-renames', '-z', ADMITTED_BASE, base)
    paths = changed_base.decode().rstrip('\0').split('\0') if changed_base else []


    _require(len(paths) == len(POLICY_FILES) and set(paths) == POLICY_FILES)
    for path in sorted(POLICY_FILES):
        entry = git('ls-tree', base, '--', path).decode().strip()
        _require(re.fullmatch(r'100644 blob [0-9a-f]{40}\t' + re.escape(path), entry) is not None)
        _require(git('show', base + ':' + path) == _LOADED_FILES[path])

    raw = git(*_DIFF_ARGS, APPROVED.baseline, APPROVED.head)
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == APPROVED.diff_sha256)
    old_source = git('show', APPROVED.baseline + ':' + APPROVED.path)
    new_source = git('show', APPROVED.head + ':' + APPROVED.path)
    _require(_assertion_line(old_source, APPROVED.old) == APPROVED.old_line)
    new_line = _assertion_line(new_source, APPROVED.new)
    names = git('diff', '--name-status', '--no-renames', '-z', APPROVED.baseline, APPROVED.head).decode().split('\0')
    _require(names[-1] == '' and len(names) % 2 == 1)
    files = [{'path': names[i + 1], 'status': 'removed' if names[i] == 'D' else 'modified'}
             for i in range(0, len(names) - 1, 2)]
    proof = VerifiedContract(APPROVED, base, digest, tuple(f['path'] for f in files),
                             tuple(f['path'] for f in files if f['status'] == 'removed'), new_line)
    return raw.decode('utf-8'), files, proof


def fetch_contract_packet(owner, repo, number, head, base):
    """Candidate-only bounded Git fetch; metadata cannot provide its own proof."""
    _require(is_candidate(owner, repo, number, head))
    _require(isinstance(base, str) and _SHA.fullmatch(base) is not None)
    deadline = time.monotonic() + 180
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0', GIT_ATTR_NOSYSTEM='1')
    try:
        with tempfile.TemporaryDirectory(prefix='karen-contract-') as directory:
            def git(*args):
                remaining = deadline - time.monotonic()
                _require(remaining > 0)
                result = subprocess.run(
                    ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.attributesFile=/dev/null', *args],
                    cwd=directory, env=env, capture_output=True, timeout=min(90, remaining))
                _require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024)
                return result.stdout
            git('init', '--bare', '-q')
            git('fetch', '--no-tags', '--filter=blob:none',
                'https://github.com/rupret007/bob-ops-dashboard.git', head, APPROVED.baseline,
                *(commit for commit, parent in BASE_ADVANCEMENTS), base)
            return _prove(base, git)
    except (OSError, ValueError, SyntaxError, UnicodeError, subprocess.SubprocessError):
        raise ContractUnavailable('Exact test-contract objects unavailable') from None


def authorized_assertion(pr):
    """Return one eligible finding, or nothing. No whole-check early PASS."""
    proof = pr.test_contract_proof
    if (type(proof) is not VerifiedContract or proof.contract != APPROVED
            or not is_candidate(pr.owner, pr.repo, pr.number, pr.head_sha)
            or not isinstance(proof.base, str) or proof.base != pr.base_sha
            or not _SHA.fullmatch(proof.base) or type(proof.new_line) is not int or proof.new_line <= 0
            or proof.diff_sha256 != APPROVED.diff_sha256
            or hashlib.sha256(pr.diff.encode()).hexdigest() != proof.diff_sha256
            or tuple(pr.changed_files) != proof.files or tuple(pr.deleted_files) != proof.deleted):
        return None
    return (APPROVED.path, APPROVED.old_line, APPROVED.old, proof.new_line, APPROVED.new)
