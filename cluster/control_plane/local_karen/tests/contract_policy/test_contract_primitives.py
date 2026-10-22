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
