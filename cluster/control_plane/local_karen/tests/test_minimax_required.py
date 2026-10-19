"""The mandatory second vote can never be bypassed or turn a rejection into PASS."""
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.reviewers import (
    MiniMaxReviewer, ReviewFindings, ReviewResult, ReviewTier, ReviewVerdict, run_review_chain,
)

MODULE = 'cluster.control_plane.local_karen.reviewers'


class RequiredMiniMaxTests(unittest.TestCase):
    def setUp(self):
        self.pr = PRInfo('owner', 'repo', 1, 'a' * 40, 'Title', 'author', False,
                         '+safe change', {}, ['file.py'], [])
        self.config = KarenConfig()
        self.findings = ReviewFindings(risk_notes=['Inspected complete diff'])

    def vote(self, verdict, reviewer='codex'):
        return ReviewResult(reviewer, verdict, 'Reviewed the complete diff', findings=self.findings)

    def test_every_primary_second_verdict_pair_is_fail_closed(self):
        for primary, second in itertools.product(ReviewVerdict, repeat=2):
            with self.subTest(primary=primary, second=second):
                reviewer = Mock()
                reviewer.review.return_value = self.vote(primary)
                with patch(MODULE + '.get_available_reviewers', return_value=[('codex', reviewer)]), \
                     patch.object(MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
                     patch.object(MiniMaxReviewer, 'review_as_second_judge', return_value=self.vote(second, 'minimax')):
                    result, _ = run_review_chain(self.pr, self.config)
                self.assertEqual(result.passed, primary == second == ReviewVerdict.PASS)

    def test_two_other_passes_and_legacy_flag_cannot_bypass_missing_minimax(self):
        reviewers = [(name, Mock(review=Mock(return_value=self.vote(ReviewVerdict.PASS, name))))
                     for name in ('codex', 'gemini')]
        for flag in (True, False):
            with patch(MODULE + '.get_available_reviewers', return_value=reviewers), \
                 patch.object(MiniMaxReviewer, 'is_available', return_value=(False, 'no key')):
                result, _ = run_review_chain(self.pr, self.config, require_two_pass=flag)
                self.assertFalse(result.passed)

    def test_second_reviewer_exception_and_missing_findings_block_pass(self):
        for second in (RuntimeError('private diagnostic'), self.vote(ReviewVerdict.PASS, 'minimax')):
            if isinstance(second, ReviewResult):
                second.findings = None
            with patch(MODULE + '.get_available_reviewers', return_value=[('codex', Mock(review=Mock(return_value=self.vote(ReviewVerdict.PASS))))]), \
                 patch.object(MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
                 patch.object(MiniMaxReviewer, 'review_as_second_judge', **(
                     {'side_effect': second} if isinstance(second, Exception) else {'return_value': second})):
                result, _ = run_review_chain(self.pr, self.config)
                self.assertFalse(result.passed)
                self.assertNotIn('private diagnostic', result.reason)

    def test_strict_response_contract(self):
        valid = dict(verdict='PASS', reason='Reviewed complete diff and found no issues',
                     confidence=0.8, findings=self.findings.to_dict())
        reviewer = MiniMaxReviewer()
        primary = self.vote(ReviewVerdict.PASS)
        good = reviewer._parse_second_judge_verdict(json.dumps(valid), self.pr, primary, 1)
        self.assertTrue(good.passed)
        for malformed in ('AGREE', 'OVERRIDE_PASS', '{}', '[]', 'null', '{',
                          json.dumps(dict(valid, findings={})),
                          json.dumps(dict(valid, confidence='yes')),
                          json.dumps(dict(valid, confidence=float('nan'))),
                          json.dumps(dict(valid, findings=dict(valid['findings'], security_issues=['bug'])))):
            with self.subTest(malformed=malformed):
                self.assertFalse(reviewer._parse_second_judge_verdict(malformed, self.pr, primary, 1).passed)

    def test_environment_precedes_existing_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / 'key'
            key_file.write_text('file-fixture')
            config = KarenConfig(minimax_api_key_file=str(key_file))
            with patch.dict(os.environ, {'MINIMAX_API_KEY': 'env-fixture'}):
                self.assertEqual(MiniMaxReviewer()._load_key(config), 'env-fixture')
            with patch.dict(os.environ, {'MINIMAX_API_KEY': ''}):
                self.assertEqual(MiniMaxReviewer()._load_key(config), 'file-fixture')

    def test_transport_errors_never_include_credentials(self):
        with patch.dict(os.environ, {'MINIMAX_API_KEY': 'secret-fixture'}), \
             patch('urllib.request.urlopen', side_effect=RuntimeError('secret-fixture')):
            result = MiniMaxReviewer().review_as_second_judge(self.pr, self.vote(ReviewVerdict.PASS), self.config)
        self.assertFalse(result.passed)
        self.assertNotIn('secret-fixture', json.dumps(result.to_dict()))

    def test_http_response_and_pinned_diff(self):
        content = json.dumps(dict(verdict='PASS', reason='Reviewed complete pinned diff with no issues',
                                  confidence=0.8, findings=self.findings.to_dict()))
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        for finish, status, passed in [('stop', 0, True), ('length', 0, False), ('stop', 1004, False)]:
            response.read.return_value = json.dumps({'base_resp': {'status_code': status}, 'choices': [
                {'finish_reason': finish, 'message': {'content': content}}]}).encode()
            with patch.dict(os.environ, {'MINIMAX_API_KEY': 'fixture'}), \
                 patch('urllib.request.urlopen', return_value=response) as urlopen:
                result = MiniMaxReviewer().review_as_second_judge(self.pr, self.vote(ReviewVerdict.PASS), self.config)
                self.assertEqual(result.passed, passed)
                request = urlopen.call_args.args[0]
                self.assertEqual(request.full_url, 'https://api.minimax.io/v1/chat/completions')
                payload = json.loads(request.data)
                self.assertEqual([message['role'] for message in payload['messages']], ['system', 'user'])
                self.assertIn(self.pr.head_sha, payload['messages'][1]['content'])
                self.assertIn(self.pr.diff, payload['messages'][1]['content'])
                self.assertEqual(payload['model'], self.config.minimax_model)
                self.assertIs(payload['reasoning_split'], True)
                self.assertNotIn('response_format', payload)
