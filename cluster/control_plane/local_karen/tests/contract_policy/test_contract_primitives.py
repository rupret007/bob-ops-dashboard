import pytest
from cluster.control_plane.local_karen import contract_policy as p

@pytest.mark.parametrize('transform', ['duplicate', 'rename', 'nested', 'different', 'reversed'])
def test_staged_assertion_parser_requires_unique_exact_direct_body(transform):
    source = 'def ' + p.APPROVED.function + '():\n' + p.APPROVED.new + '\n'
    if transform == 'duplicate':
        source += source
    elif transform == 'rename':
        source = source.replace(p.APPROVED.function, 'test_elsewhere')
    elif transform == 'nested':
        source = source.replace(p.APPROVED.new, '    if False:\n    ' + p.APPROVED.new)
    elif transform == 'different':
        source = source.replace("'none'", "'else'")
    elif transform == 'reversed':
        source = source.replace(p.APPROVED.new, p.APPROVED.old)
    with pytest.raises(p.ContractUnavailable):
        p._assertion_line(source.encode(), p.APPROVED.new)


def test_empty_snapshot_cannot_match_even_a_matching_runtime(monkeypatch):
    base = 'a'*40
    monkeypatch.setattr(p, 'POLICY_FILES', frozenset())
    monkeypatch.setattr(p, '_LOADED_FILES', {})
    monkeypatch.setattr(p, '_LOADED_BASE', base)
    monkeypatch.setattr(p, 'deployment_status', lambda: dict(
        source_matches=True, installed_head=base, loaded_head=base))
    assert not p.runtime_base_matches(base)


def test_empty_snapshot_refuses_before_reading_any_object(monkeypatch):
    base = 'a'*40
    monkeypatch.setattr(p, 'POLICY_FILES', frozenset())
    monkeypatch.setattr(p, '_LOADED_FILES', {})
    monkeypatch.setattr(p, '_LOADED_BASE', base)
    monkeypatch.setattr(p, 'deployment_status', lambda: dict(
        source_matches=True, installed_head=base, loaded_head=base))
    reads = []
    def forbidden_reader(*args):
        reads.append(args)
        raise AssertionError('Empty snapshot reached an object reader')
    with pytest.raises(p.ContractUnavailable):
        p._prove(base, forbidden_reader)
    assert not reads
