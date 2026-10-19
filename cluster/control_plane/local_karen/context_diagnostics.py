"""Bounded operational metadata; never review evidence or approval authority."""
from .source_context import SourceUnavailable

MAX_DIAGNOSTICS = 32
REVIEWERS = frozenset(('codex', 'gemini', 'claude', 'minimax'))
CONTEXT_CODES = frozenset(('not_requested', 'invalid_request', 'context_budget',
                          'changed_unavailable', 'source_unavailable',
                          'context_supplied', 'followup_complete'))


def sanitize_context_diagnostics(records):
    """Copy only fixed enums and bounded integers, including at persistence."""
    if type(records) not in (list, tuple):
        return []
    safe = []
    for record in records[:MAX_DIAGNOSTICS]:
        if type(record) is not dict or type(record.get('reviewer')) is not str:
            continue
        if record['reviewer'] not in REVIEWERS:
            continue
        item = {'reviewer': record['reviewer']}
        for key, allowed in (('source_context_error', SourceUnavailable.CODES),
                             ('context_code', CONTEXT_CODES)):
            if key in record:
                value = record[key]
                item[key] = value if type(value) is str and value in allowed else 'unknown'
        for key, low, high in (('chunk', 1, 4096), ('changed_requests', 0, 4),
                               ('source_requests', 0, 4)):
            value = record.get(key)
            if type(value) is int and low <= value <= high:
                item[key] = value
        if len(item) > 1:
            safe.append(item)
    return safe


def collect_context_diagnostics(reviews):
    """Extract no paths, reasons, findings, raw replies or source values."""
    if type(reviews) not in (list, tuple):
        return []
    records = []
    for review in reviews[:MAX_DIAGNOSTICS]:
        scope = getattr(review, 'scope', None)
        if type(scope) is not dict:
            continue
        record = {'reviewer': getattr(review, 'reviewer', None)}
        for key in ('chunk', 'source_context_error'):
            if key in scope:
                record[key] = scope[key]
        resolution = scope.get('context_resolution')
        if type(resolution) is dict:
            if 'code' in resolution:
                record['context_code'] = resolution['code']
            for key in ('changed_requests', 'source_requests'):
                if key in resolution:
                    record[key] = resolution[key]
        records.append(record)
    return sanitize_context_diagnostics(records)
