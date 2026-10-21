"""Metadata-only proof of exclusions in server-owned Pages publication profiles.

Unknown pipelines fail closed. Profiles never come from PR text or configuration
in the reviewed repository. No source is downloaded or executed. Matching proves
the stated exclusion, not deployment, PR approval or dependency supply-chain safety.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Tuple

from .. import github_reads
from ..github_budget import GitHubDeferred
from .pages_profile_recipes import recipe_blob_id

UNKNOWN = 'unknown'
WORKFLOW = 'workflow'
CHECK_SECONDS = 20
SETTINGS_BYTES = COMMIT_BYTES = 64 * 1024
TREE_BYTES = 2 * 1024 * 1024
SHA = re.compile(r'[0-9a-f]{40}')


@dataclass(frozen=True)
class PublicationProfile:
    name: str
    fingerprints: Tuple[Tuple[str, str], ...]
    excluded_prefixes: Tuple[str, ...] = ('docs/',)


# Independently reviewed pipeline c41896b12ba7743d4f362869d5ea3bc9709038b1:
# reconstructed artifact 206 files / 35,559,941 bytes, docs absent; manifest SHA256
# 7afc395cd3e98c1f01716ab176073e736df308aacd406a01992d65c150907cb4.
# b6a0c10933b4522dacd7b471e219c556edb25aa4 adds reviewed nonpublishing health
# workflow. These Git blob pins are independent of repository name. The fixed npm
# dependencies/lifecycles and checkout attributes are reviewed baseline trust.
_BASE = (
    ('.github/workflows/ci.yml', recipe_blob_id('.github/workflows/ci.yml')),
    ('scripts/package-pages.cjs', recipe_blob_id('scripts/package-pages.cjs')),
    ('package.json', recipe_blob_id('package.json')),
    # Baseline dependency/lifecycle trust remains opaque, not a safety claim.
    ('package-lock.json', '2c929906f481401ad8bd6a4d67fb56bdea9cbfeb'),
    ('.gitattributes', recipe_blob_id('.gitattributes')),
)
PROFILES = (
    PublicationProfile('reviewed-dist-v1', _BASE),
    PublicationProfile('reviewed-dist-health-v1', _BASE + (
        ('.github/workflows/beta-health.yml', recipe_blob_id('.github/workflows/beta-health.yml')),
    )),
)


class UnverifiedPublication(ValueError):
    pass


def _repo(owner, repo):
    if (not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner)
            or not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', repo)
            or repo in ('.', '..')):
        raise UnverifiedPublication('Invalid repository identity')
    return owner + '/' + repo


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise UnverifiedPublication('Duplicate metadata key')
        value[key] = item
    return value


def _read(endpoint, limit, deadline):
    """Uncached, deadline-bound read through the canonical shared API budget."""
    if time.monotonic() >= deadline:
        raise UnverifiedPublication('Publication deadline exhausted')
    response = github_reads.run(['api', '--include', '--method', 'GET', endpoint],
                                timeout=10, resource='core', deadline=deadline)
    raw = response.stdout
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > limit + 8192:
        raise UnverifiedPublication('Metadata response exceeds bound')
    header, separator, body = raw.replace('\r\n', '\n').partition('\n\n')
    if not separator or len(header.encode('utf-8')) > 8192:
        raise UnverifiedPublication('Missing response status')
    status = re.fullmatch(r'HTTP/\S+ (\d{3})(?: [^\n]*)?', header.split('\n')[0])
    if not status or len(body.encode('utf-8')) > limit:
        raise UnverifiedPublication('Invalid response status or bound')
    code = int(status.group(1))
    if code not in (200, 404) or (code == 200 and response.returncode != 0):
        raise UnverifiedPublication('Metadata request failed')
    value = json.loads(body, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise UnverifiedPublication('Expected metadata object')
    return code, value


_READ_ERRORS = (ValueError, TypeError, UnicodeError, OSError, RecursionError, subprocess.SubprocessError,
                github_reads.GitHubReadError, GitHubDeferred)


def pages_source(owner, repo, *, deadline=None):
    """Classify workflow/legacy path/confirmed off; a 404 alone is unknown."""
    deadline = deadline if deadline is not None else time.monotonic() + CHECK_SECONDS
    try:
        name = _repo(owner, repo)
        status, data = _read('repos/' + name + '/pages', SETTINGS_BYTES, deadline)
        if status == 404:
            status, data = _read('repos/' + name, SETTINGS_BYTES, deadline)
            if (status == 200 and isinstance(data.get('full_name'), str)
                    and data['full_name'].casefold() == name.casefold()
                    and data.get('has_pages') is False):
                return None
            return UNKNOWN
        if data.get('build_type') == WORKFLOW:
            return WORKFLOW
        source = data.get('source')
        if data.get('build_type') == 'legacy' and isinstance(source, dict):
            return source.get('path') if source.get('path') in ('/', '/docs') else UNKNOWN
    except _READ_ERRORS:
        pass
    return UNKNOWN


def publication_input(path):
    """Recognized inputs of the supported publisher, not arbitrary build systems."""
    lower = path.casefold()
    parts = lower.split('/')
    return (lower == '.github/workflows' or lower.startswith('.github/workflows/')
            or lower in ('scripts/package-pages.cjs', 'package.json', 'package-lock.json',
                         'npm-shrinkwrap.json', 'binding.gyp')
            or parts[-1] in ('.gitattributes', '.npmrc') or 'node_modules' in parts)


def _path(value):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 1024:
        raise UnverifiedPublication('Invalid tree path')
    parts = value.split('/')
    if ('\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(not p or p in ('.', '..') or len(p.encode('utf-8')) > 255 for p in parts)):
        raise UnverifiedPublication('Noncanonical tree path')
    return parts


def _tree_entries(data, tree_sha):
    if data.get('sha') != tree_sha or data.get('truncated') is not False:
        raise UnverifiedPublication('Unbound or incomplete tree')
    entries = data.get('tree')
    if not isinstance(entries, list) or not entries or len(entries) > 10000:
        raise UnverifiedPublication('Invalid tree entries')
    result = {}
    for item in entries:
        if not isinstance(item, dict):
            raise UnverifiedPublication('Invalid tree entry')
        path = item.get('path')
        parts = _path(path)
        if path in result or not isinstance(item.get('sha'), str) or not SHA.fullmatch(item['sha']):
            raise UnverifiedPublication('Duplicate path or invalid object identity')
        kind, mode = item.get('type'), item.get('mode')
        if not ((kind == 'tree' and mode == '040000')
                or (kind == 'blob' and mode in ('100644', '100755'))):
            raise UnverifiedPublication('Nonregular publication input')
        if kind == 'blob' and (type(item.get('size')) is not int or item['size'] < 0):
            raise UnverifiedPublication('Invalid blob metadata')
        lower = [part.casefold() for part in parts]
        if any(part in ('.git', '.npmrc', 'node_modules') for part in lower):
            raise UnverifiedPublication('Unreviewed build configuration')
        if len(parts) == 1 and lower[0] in ('npm-shrinkwrap.json', 'binding.gyp'):
            # Shrinkwrap overrides the lock; binding.gyp adds an implicit npm
            # install lifecycle even though the pinned package has no install.
            raise UnverifiedPublication('Unreviewed install lifecycle input')
        result[path] = item
    for path in result:
        parent = path.rpartition('/')[0]
        if parent and (parent not in result or result[parent]['type'] != 'tree'):
            raise UnverifiedPublication('Missing tree ancestor')
    # Reconstruct tree identities without retrieving any blob/source contents.
    children = {'': []}
    for path, item in result.items():
        parent, _, base = path.rpartition('/')
        children.setdefault(parent, []).append((base, item))
        if item['type'] == 'tree':
            children.setdefault(path, [])
    for directory, members in children.items():
        members.sort(key=lambda pair: (pair[0] + ('/' if pair[1]['type'] == 'tree' else '')).encode('utf-8'))
        body = b''.join((('40000' if item['type'] == 'tree' else item['mode']) + ' ').encode('ascii')
                        + base.encode('utf-8') + b'\0' + bytes.fromhex(item['sha'])
                        for base, item in members)
        digest = hashlib.sha1(b'tree ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest()
        if digest != (result[directory]['sha'] if directory else tree_sha):
            raise UnverifiedPublication('Tree object identity mismatch')
    return result


def approved_docs_exclusion(owner, repo, head_sha, *, deadline=None):
    """Prove only docs/ exclusion from the complete immutable exact-head tree."""
    deadline = deadline if deadline is not None else time.monotonic() + CHECK_SECONDS
    try:
        name = _repo(owner, repo)
        if not isinstance(head_sha, str) or not SHA.fullmatch(head_sha):
            return False
        status, commit = _read('repos/' + name + '/git/commits/' + head_sha, COMMIT_BYTES, deadline)
        tree = commit.get('tree')
        if status != 200 or commit.get('sha') != head_sha or not isinstance(tree, dict):
            return False
        tree_sha = tree.get('sha')
        if not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha):
            return False
        status, data = _read('repos/' + name + '/git/trees/' + tree_sha + '?recursive=1', TREE_BYTES, deadline)
        if status != 200:
            return False
        entries = _tree_entries(data, tree_sha)
        attributes = {p for p in entries if p.rsplit('/', 1)[-1].casefold() == '.gitattributes'}
        if attributes != {'.gitattributes'}:
            return False
        workflows = {p for p, item in entries.items() if p.startswith('.github/workflows/') and item['type'] != 'tree'}
        for profile in PROFILES:
            expected = {p for p, _ in profile.fingerprints if p.startswith('.github/workflows/')}
            if workflows != expected or profile.excluded_prefixes != ('docs/',):
                continue
            if all(p in entries and entries[p]['type'] == 'blob' and entries[p]['mode'] == '100644'
                   and entries[p]['sha'] == sha for p, sha in profile.fingerprints):
                return True
    except _READ_ERRORS:
        pass
    return False
