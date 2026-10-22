"""A repair needs the concrete defect, not only a count of rejected checks."""
from cluster.control_plane.fix_dispatcher import extract_findings_from_verdict


def test_failed_check_forwards_redacted_file_line_evidence():
    findings = extract_findings_from_verdict({'check_results': [{
        'check': 'no_dangerous_commands', 'verdict': 'FAIL',
        'reason': 'Dangerous commands detected: 1 issue(s)',
        'details': 'scripts/deploy.sh:17: privileged command (source redacted)',
    }]})
    assert any('scripts/deploy.sh:17' in item for item in findings)


def test_failed_review_forwards_structured_defects_without_raw_response():
    findings = extract_findings_from_verdict({'review_results': [{
        'reviewer': 'codex', 'verdict': 'FAIL', 'reason': 'Input validation is unsafe',
        'findings': {'functional_issues': ['src/app.py:22 passes unchecked user input'],
                     'risk_notes': ['Implementation context was omitted']},
        'raw_response': 'untrusted raw provider payload',
    }]})
    assert any('src/app.py:22' in item for item in findings)
    assert not any('omitted' in item or 'raw provider' in item for item in findings)


def test_non_fail_and_context_only_reviews_do_not_authorize_repair():
    findings = extract_findings_from_verdict({'review_results': [
        {'verdict': 'ERROR', 'reason': 'network failure'},
        {'verdict': 'BORDERLINE', 'reason': 'missing installer context'},
        {'verdict': 'PASS', 'reason': 'correct'},
        {'verdict': 'FAIL', 'reason': 'missing chunk context',
         'scope': {'context_incomplete': True},
         'findings': {'functional_issues': ['src/app.py:22 omitted from this chunk']}},
    ]})
    assert findings == []


def test_failed_secret_check_never_forwards_credential_evidence_for_auto_fix():
    value = 'ghp_' + 'B7c9' * 9
    assert extract_findings_from_verdict({'check_results': [{
        'check': 'no_secrets', 'verdict': 'FAIL',
        'reason': 'Potential secrets detected', 'details': 'config.py:17: ' + value,
    }]}) == []
