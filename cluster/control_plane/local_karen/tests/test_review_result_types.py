"""Existing consumers and direct imports share one set of result types."""
import json
from unittest.mock import Mock

import pytest

from cluster.control_plane.local_karen import review_result_types as types, reviewers


@pytest.mark.parametrize('name', ['ReviewTier', 'ReviewVerdict', 'ReviewFindings', 'ReviewResult'])
def test_public_provider_and_direct_consumer_types_have_one_identity(name):
    assert getattr(reviewers, name) is getattr(types, name)


def test_existing_parser_returns_directly_imported_result_and_findings_types():
    parser = reviewers.MiniMaxReviewer()
    primary = types.ReviewResult('fixture', types.ReviewVerdict.PASS, 'Checked',
                                findings=types.ReviewFindings(risk_notes=['Checked diff']))
    payload = dict(verdict='PASS', reason='Checked complete pinned diff with no issues',
                   confidence=0.9, findings=primary.findings.to_dict())
    result = parser._parse_second_judge_verdict(json.dumps(payload), Mock(), primary, 1)
    assert isinstance(result, types.ReviewResult)
    assert isinstance(result.findings, types.ReviewFindings)
    assert result.verdict is types.ReviewVerdict.PASS and result.passed
    wire = json.loads(json.dumps(result.to_dict()))
    assert wire['verdict'] == 'PASS' and wire['tier'] == result.tier.value
    assert wire['findings']['risk_notes'] == ['Checked diff']
