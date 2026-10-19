"""Passing security checks must not strand ordinary repairs for Jeff."""
from cluster.control_plane.fix_dispatcher import classify_fix_difficulty


def test_passed_secret_checks_do_not_block_low_risk_repair():
    assert classify_fix_difficulty({'tier': 'low', 'check_results': [
        {'check': 'no_secrets', 'verdict': 'PASS', 'reason': 'No secrets detected in diff'},
        {'check': 'static_security_scan', 'verdict': 'PASS', 'reason': 'Static security scan clean'},
        {'check': 'no_dangerous_commands', 'verdict': 'FAIL',
         'reason': 'Dangerous commands detected: 1 issue(s)'},
    ], 'review_results': [
        {'verdict': 'PASS', 'reason': 'No authentication or injection issues found'},
    ]}) == 'low'


def test_failed_secret_or_security_check_still_requires_human():
    for reason in ('Potential secrets detected', 'Authorization bypass', 'Injection vulnerability'):
        assert classify_fix_difficulty({'check_results': [
            {'verdict': 'FAIL', 'reason': reason},
        ]}) == 'security'


def test_review_security_finding_requires_human_even_with_generic_reason():
    assert classify_fix_difficulty({'review_results': [{
        'verdict': 'FAIL', 'reason': 'Blocking defect found',
        'findings': {'security_issues': ['app.py:21 accepts an unchecked shell argument']},
    }]}) == 'security'


def test_security_and_high_tiers_keep_their_existing_guards():
    assert classify_fix_difficulty({'tier': 'security'}) == 'security'
    assert classify_fix_difficulty({'tier': 'high'}) == 'high'
