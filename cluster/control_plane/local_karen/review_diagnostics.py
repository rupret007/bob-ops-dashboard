"""Bounded structural diagnostics for unpublished review errors.

Only server-owned context scope fields are eligible. Model text, requested paths,
source provenance/content and whole scope objects are never retained here.
"""
from __future__ import annotations

MAX_DIAGNOSTICS = 16
MAX_SCOPES = 32
INITIAL_CODES = frozenset(('not_applicable', 'not_needed', 'supplied',
                           'changed_unavailable', 'context_budget'))
RESOLUTION_CODES = frozenset(('not_requested', 'invalid_request', 'context_budget',
                              'changed_unavailable', 'source_unavailable',
                              'context_supplied', 'followup_complete'))
SOURCE_CODES = frozenset(('source_budget', 'source_read', 'response_size',
                          'response_shape', 'source_identity_or_path', 'commit_identity',
                          'tree_identity_or_incomplete', 'tree_shape', 'source_metadata',
                          'blob_identity', 'source_encoding', 'blob_hash', 'source_nontext',
                          'source_private', 'source_unavailable'))


def _integer(value, maximum, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def _context(value, codes, limits):
    if type(value) is not dict:
        return {}
    result = {}
    code = value.get('code')
    if type(code) is str and code in codes:
        result['code'] = code
    for name, maximum in limits.items():
        number = value.get(name)
        if _integer(number, maximum):
            result[name] = number
    return result


def sanitize_context_diagnostics(scopes):
    """Copy a fixed schema, rejecting arbitrary strings/types and excess entries."""
    if type(scopes) not in (list, tuple):
        return []
    result = []
    for scope in scopes[:MAX_SCOPES]:
        if type(scope) is not dict:
            continue
        item = {}
        chunk, total = scope.get('chunk'), scope.get('total')
        if _integer(chunk, 1000000, 1) and _integer(total, 1000000, 1) and chunk <= total:
            item.update(chunk=chunk, total=total)
        initial = _context(scope.get('initial_context'), INITIAL_CODES,
                           {'changed_files': 4, 'raw_size': 48000, 'serialized_size': 48000,
                            'chunk_characters': 96000, 'pr_changed_files': 1000000})
        if initial:
            item['initial_context'] = initial
        resolution = _context(scope.get('context_resolution'), RESOLUTION_CODES,
                              {'changed_requests': 4, 'source_requests': 4})
        if resolution:
            item['context_resolution'] = resolution
        source = scope.get('source_context_error')
        if type(source) is str and source in SOURCE_CODES:
            item['source_context_error'] = source
        if item and item not in result:
            result.append(item)
            if len(result) == MAX_DIAGNOSTICS:
                break
    return result


def review_error_diagnostics(result, reviews):
    # Put the terminal outcome first so truncation cannot hide its blocking
    # chunk. Individual attempts retain context when the chain drops its scope.
    scopes = [getattr(result, 'scope', None)]
    if type(reviews) in (list, tuple):
        scopes.extend(getattr(review, 'scope', None) for review in reviews[:MAX_SCOPES - 1])
    return sanitize_context_diagnostics(scopes)
