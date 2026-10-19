"""Additive metadata detail preserves the legacy source error and review boundary."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import gate, reviewers as r, source_context as s
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.context_diagnostics import (
    collect_context_diagnostics, sanitize_context_diagnostics,
)
from cluster.control_plane.local_karen.tests.test_chunk_evidence import pr_info, vote
from cluster.control_plane.local_karen.tests.test_source_context import Objects

PRIVATE = 'synthetic-private-value-never-retain'
REASONS = (
    'source_entry_missing', 'source_parent_metadata', 'source_type_or_mode',
    'source_size_invalid', 'source_file_limit', 'source_total_limit', 'source_blob_sha',
)
CASES = (
    ('missing-parent', 'source_parent_metadata'), ('missing-entry', 'source_entry_missing'),
    ('parent-mode', 'source_parent_metadata'), ('parent-type', 'source_parent_metadata'),
    ('type', 'source_type_or_mode'), ('symlink', 'source_type_or_mode'),
    ('submodule', 'source_type_or_mode'), ('bool-size', 'source_size_invalid'),
    ('negative-size', 'source_size_invalid'), ('missing-size', 'source_size_invalid'),
    ('string-size', 'source_size_invalid'), ('oversize', 'source_file_limit'),
    ('total', 'source_total_limit'), ('missing-sha', 'source_blob_sha'),
    ('invalid-sha', 'source_blob_sha'),
)


def rejecting_objects(monkeypatch, kind):
    objects = Objects()
    entry, parent = objects.tree['tree']
    resolver = s.PinnedSourceContext(pr_info())
    if kind == 'missing-parent': objects.tree['tree'] = []
    elif kind == 'missing-entry': objects.tree['tree'] = [parent]
    elif kind == 'parent-mode': parent['mode'] = '120000'
    elif kind == 'parent-type': parent['type'] = 'blob'
    elif kind == 'type': entry['type'] = 'tree'
    elif kind in ('symlink', 'submodule'): entry['mode'] = '120000' if kind == 'symlink' else '160000'
    elif kind == 'bool-size': entry['size'] = True
    elif kind == 'negative-size': entry['size'] = -1
    elif kind == 'missing-size': del entry['size']
    elif kind == 'string-size': entry['size'] = PRIVATE
    elif kind == 'oversize': entry['size'] = 16001
    elif kind == 'total': resolver.bytes = 48000
    elif kind == 'missing-sha': del entry['sha']
    elif kind == 'invalid-sha': entry['sha'] = PRIVATE
    else: raise AssertionError('unknown test case')
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    return objects, resolver


@pytest.mark.parametrize('kind, detail', CASES)
def test_actual_metadata_rejections_keep_legacy_code_and_add_fixed_detail(monkeypatch, caplog, kind, detail):
    objects, resolver = rejecting_objects(monkeypatch, kind)
    used = resolver.bytes
    with pytest.raises(s.SourceUnavailable) as error:
        resolver.fetch(pr_info(), 'src/runtime.py')
    assert error.value.code == str(error.value) == 'source_metadata'
    assert error.value.args == ('source_metadata',)
    assert error.value.metadata_reason == detail
    assert len(objects.calls) == 2  # Only commit/tree metadata; never the rejected blob.
    assert resolver.bytes == used and resolver.sources == {}
    for private in (PRIVATE, 'src/runtime.py', objects.raw.decode(), 'private stderr'):
        assert private not in str(error.value) and private not in caplog.text


@pytest.mark.parametrize('parent_mode, fields, used, detail', [
    ('120000', {'type': 'tree', 'size': -1, 'sha': None}, 48000, 'source_parent_metadata'),
    ('040000', {'type': 'tree', 'size': -1, 'sha': None}, 48000, 'source_type_or_mode'),
    ('040000', {'mode': '120000', 'size': -1, 'sha': None}, 48000, 'source_type_or_mode'),
    ('040000', {'size': -1, 'sha': None}, 48000, 'source_size_invalid'),
    ('040000', {'size': 16001, 'sha': None}, 48000, 'source_file_limit'),
    ('040000', {'size': 1, 'sha': None}, 48000, 'source_total_limit'),
    ('040000', {'size': 0, 'sha': None}, 48000, 'source_blob_sha'),
])
def test_metadata_checks_preserve_short_circuit_precedence(monkeypatch, parent_mode, fields, used, detail):
    objects = Objects()
    objects.tree['tree'][1]['mode'] = parent_mode
    objects.tree['tree'][0].update(fields)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    resolver = s.PinnedSourceContext(pr_info())
    resolver.bytes = used
    with pytest.raises(s.SourceUnavailable) as error:
        resolver.fetch(pr_info(), 'src/runtime.py')
    assert error.value.code == 'source_metadata' and error.value.metadata_reason == detail
    assert len(objects.calls) == 2
    assert resolver.bytes == used and resolver.sources == {}


def test_exact_file_and_total_boundaries_and_cache_are_unchanged(monkeypatch):
    objects = Objects(raw=b'x' * 16000)
    entry, parent = objects.tree['tree']
    objects.tree['tree'] = [dict(entry, path='src/file{}.py'.format(n)) for n in range(4)] + [parent]
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    resolver = s.PinnedSourceContext(pr_info())
    for n in range(3):
        assert resolver.fetch(pr_info(), 'src/file{}.py'.format(n))['bytes'] == 16000
    assert resolver.bytes == 48000 and len(objects.calls) == 5
    assert resolver.fetch(pr_info(), 'src/file0.py')['bytes'] == 16000
    with pytest.raises(s.SourceUnavailable) as error:
        resolver.fetch(pr_info(), 'src/file3.py')
    assert error.value.code == str(error.value) == 'source_metadata'
    assert error.value.metadata_reason == 'source_total_limit'
    assert resolver.bytes == 48000 and len(objects.calls) == 5


@pytest.mark.parametrize('detail', REASONS)
def test_metadata_detail_is_optional_and_never_changes_exception_text(detail):
    legacy = s.SourceUnavailable('source_metadata')
    precise = s.SourceUnavailable('source_metadata', metadata_reason=detail)
    assert legacy.code == precise.code == str(legacy) == str(precise) == 'source_metadata'
    assert legacy.args == precise.args == ('source_metadata',)
    assert legacy.metadata_reason is None and precise.metadata_reason == detail
    assert s.SourceUnavailable('blob_identity', metadata_reason=detail).metadata_reason is None
    assert s.SourceUnavailable('unknown', metadata_reason=detail).metadata_reason is None


@pytest.mark.parametrize('detail', [None, PRIVATE, [], {}, True, 1, ['source_file_limit']])
def test_untrusted_detail_is_not_retained_on_exception(detail):
    error = s.SourceUnavailable('source_metadata', metadata_reason=detail)
    assert error.metadata_reason is None
    assert str(error) == 'source_metadata' and error.args == ('source_metadata',)
    assert PRIVATE not in json.dumps(vars(error))


@pytest.mark.parametrize('kind, detail', CASES)
def test_actual_unavailable_source_has_no_followup_or_pass_and_serializes_safe_detail(monkeypatch, caplog, kind, detail):
    objects, resolver = rejecting_objects(monkeypatch, kind)
    initial = replace(vote(), requested_source_files=['src/runtime.py'],
                      reason=PRIVATE, raw_response=PRIVATE)
    invoke = Mock(return_value=initial)
    caplog.set_level('INFO', logger=r.__name__)
    result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info(), resolver)
    assert invoke.call_count == 1 and len(objects.calls) == 2
    assert result.verdict == r.ReviewVerdict.BORDERLINE and not result.passed
    assert result.scope['context_incomplete'] is True
    assert result.scope['source_context_error'] == 'source_metadata'
    assert result.scope['source_metadata_reason'] == detail
    safe = collect_context_diagnostics([result])
    assert safe == [{'reviewer': 'codex', 'source_context_error': 'source_metadata',
                     'source_metadata_reason': detail, 'context_code': 'source_unavailable',
                     'changed_requests': 0, 'source_requests': 1}]
    assert 'Source context unavailable: code=source_metadata' in caplog.text
    for private in (PRIVATE, 'src/runtime.py', objects.raw.decode(), 'private stderr'):
        assert private not in json.dumps(safe) and private not in caplog.text


@pytest.mark.parametrize('detail', REASONS)
def test_collector_and_json_roundtrip_keep_only_coupled_allowlisted_detail(detail):
    scope = {'source_context_error': 'source_metadata', 'source_metadata_reason': detail,
             'chunk': 2, 'context_resolution': {'code': 'source_unavailable',
                                               'changed_requests': 0, 'source_requests': 1},
             'path': PRIVATE, 'source': PRIVATE, 'reason': PRIVATE, 'exception': PRIVATE,
             'raw_response': PRIVATE, 'passed': True, 'verdict': 'PASS', 'sha_verified': True}
    safe = collect_context_diagnostics([SimpleNamespace(reviewer='codex', scope=scope)])
    expected = [{'reviewer': 'codex', 'source_context_error': 'source_metadata',
                 'source_metadata_reason': detail, 'context_code': 'source_unavailable',
                 'chunk': 2, 'changed_requests': 0, 'source_requests': 1}]
    assert safe == expected
    assert sanitize_context_diagnostics(json.loads(json.dumps(safe))) == expected
    assert PRIVATE not in json.dumps(safe)
    assert len(sanitize_context_diagnostics(safe * 100)) == 32


@pytest.mark.parametrize('primary', [None, 'source_read', 'blob_identity', PRIVATE, [], {}])
def test_metadata_detail_requires_the_legacy_metadata_primary_code(primary):
    record = {'reviewer': 'codex', 'source_metadata_reason': 'source_file_limit',
              'context_code': 'source_unavailable'}
    if primary is not None:
        record['source_context_error'] = primary
    safe = sanitize_context_diagnostics([record])
    assert len(safe) == 1 and 'source_metadata_reason' not in safe[0]
    assert PRIVATE not in json.dumps(safe)


@pytest.mark.parametrize('detail', [None, PRIVATE, [], {}, True, 1, ['source_file_limit']])
def test_sanitizer_omits_untrusted_detail_and_preserves_old_record_shape(detail):
    record = {'reviewer': 'codex', 'source_context_error': 'source_metadata',
              'source_metadata_reason': detail, 'reason': PRIVATE, 'exception': PRIVATE}
    assert sanitize_context_diagnostics([record]) == [
        {'reviewer': 'codex', 'source_context_error': 'source_metadata'}]
    assert sanitize_context_diagnostics([{'reviewer': 'codex', 'source_context_error': 'source_metadata'}]) == [
        {'reviewer': 'codex', 'source_context_error': 'source_metadata'}]


@pytest.mark.parametrize('primary, detail', [
    ('source_metadata', None), ('source_metadata', PRIVATE),
    ('source_metadata', {}), ('source_read', 'source_file_limit'),
])
def test_reviewer_does_not_inherit_or_emit_uncoupled_metadata_detail(primary, detail, caplog):
    initial = replace(vote(), requested_source_files=['src/runtime.py'],
                      scope={'source_metadata_reason': PRIVATE})
    error = s.SourceUnavailable(primary)
    error.metadata_reason = detail  # Recheck even a mutated exception at the logging boundary.
    resolver = Mock(fetch=Mock(side_effect=error))
    caplog.set_level('INFO', logger=r.__name__)
    result, _ = r._review_with_requested_context(Mock(return_value=initial), pr_info(), pr_info(), resolver)
    assert result.scope['source_context_error'] == primary
    assert 'source_metadata_reason' not in result.scope
    assert PRIVATE not in caplog.text
    assert PRIVATE not in json.dumps(collect_context_diagnostics([result]))


def test_safe_detail_does_not_turn_mocked_gate_error_into_approval(monkeypatch, tmp_path):
    _, resolver = rejecting_objects(monkeypatch, 'missing-entry')
    initial = replace(vote(), requested_source_files=['src/runtime.py'])
    unresolved, _ = r._review_with_requested_context(Mock(return_value=initial), pr_info(), pr_info(), resolver)
    config = KarenConfig(state_dir=str(tmp_path), verdict_dir=str(tmp_path))
    with patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'run_review_chain', return_value=(unresolved, [unresolved])), \
         patch.object(gate, '_metrics'), patch.object(gate, 'save_verdict') as save, \
         patch.object(gate, 'verify_sha_current') as verify:
        result = gate.GateRunner(config)._run_claimed(pr_info(), False, 0)
    assert result.verdict.status == 'ERROR' and not result.verdict.passed
    assert result.verdict_path is None and not result.sha_verified
    assert result.verdict.review_results == []
    assert result.context_diagnostics[0]['source_metadata_reason'] == 'source_entry_missing'
    save.assert_not_called()
    verify.assert_not_called()
    assert not list(tmp_path.glob('*.json'))
