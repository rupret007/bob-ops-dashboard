import copy
import json
from pathlib import Path
import tempfile
import unittest
from scorecards import THRESHOLDS, build_scorecards, status, scorecards_html, normalize_scorecards
from scorecard_sources import session_records, gemini_records, provider_records, note_records, github_records

FIXTURE = json.loads(Path('tests/fixtures/scorecards/evidence.json').read_text())


class ComputationTests(unittest.TestCase):
    def setUp(self):
        self.records = copy.deepcopy(FIXTURE['records'])
        self.now = FIXTURE['now']

    def test_every_metric_with_fixture(self):
        data = build_scorecards(self.records, now=self.now)
        card = data['windows']['24h']['providers']['codex']
        expected = {'karen_first_pass_pct':50, 'karen_rounds':1.5, 'ci_first_push_pct':50,
                    'rollback_48h_pct':100, 'gemini_agreement_pct':33.3333, 'gemini_missed':1,
                    'gemini_false_alarm':1, 'stuck_prs':1, 'tokens':200, 'usd':10,
                    'codex_weekly_pct':42, 'tokens_per_merge':200, 'usd_per_merge':10,
                    'tokens_per_pass':100, 'usd_per_pass':5, 'wasted_runs':1,
                    'empty_gemini':1, 'refusals':1}
        self.assertEqual(set(card), set(expected))
        for name, value in expected.items():
            self.assertEqual(card[name]['value'], value, name)
            self.assertEqual(card[name]['status'], status(name, value))
        self.assertEqual(data['windows']['7d']['providers']['codex']['rollback_48h_pct']['value'], 100)
        self.assertEqual(data['windows']['7d']['providers']['codex']['usd_per_merge']['value'], 5)
        self.assertEqual(data['windows']['24h']['repos']['owner/repo'], card)
        self.assertEqual(data['windows']['24h']['idle_slot_hours']['mini']['value'], 2)
        self.assertIsNone(data['windows']['24h']['idle_slot_hours']['nucbox']['value'])

    def test_every_metric_no_data_and_grok_unknown(self):
        data = build_scorecards([], now=self.now, repos=['owner/repo'])
        for window in data['windows'].values():
            for category in ('providers', 'repos'):
                for label, card in window[category].items():
                    for name, item in card.items():
                        self.assertIsNone(item['value'], (label, name))
                        self.assertEqual(item['status'], 'unknown' if label == 'grok_bot' else 'no data')
            for item in window['idle_slot_hours'].values():
                self.assertIsNone(item['value'])
        html = scorecards_html(data)
        self.assertIn('Grok Bot (conserve-only)', html)
        self.assertIn('no data', html)

    def test_every_threshold_at_and_either_side_of_boundaries(self):
        for name, (direction, good, watch) in THRESHOLDS.items():
            with self.subTest(name=name):
                self.assertEqual(status(name, None), 'no data')
                self.assertEqual(status(name, float('nan')), 'no data')
                self.assertEqual(status(name, True), 'no data')
                self.assertEqual(status(name, good), 'good')
                self.assertEqual(status(name, watch), 'watch')
                delta = .01 if direction == 'low' else -.01
                self.assertEqual(status(name, good + delta), 'watch')
                self.assertEqual(status(name, watch + delta), 'bad')

    def test_rolling_boundaries_missing_spend_and_group_isolation(self):
        self.records += [dict(kind='spend', provider='codex', at=self.now-86400, tokens=9, usd=3),
                         dict(kind='spend', provider='gemini', at=self.now-1, tokens=999, usd=99),
                         dict(kind='spend', provider='codex', at=self.now+1, tokens=999, usd=99)]
        data = build_scorecards(self.records, now=self.now)
        self.assertEqual(data['windows']['24h']['providers']['codex']['tokens']['value'], 200)
        self.assertEqual(data['windows']['7d']['providers']['codex']['tokens']['value'], 209)
        self.records.append(dict(kind='spend', provider='codex', at=self.now-1, tokens=None, usd=None))
        card = build_scorecards(self.records, now=self.now)['windows']['24h']['providers']['codex']
        for name in ('tokens', 'usd', 'tokens_per_merge', 'usd_per_pass'):
            self.assertIsNone(card[name]['value'])
            self.assertTrue(card[name]['partial'])

    def test_unknown_rounds_heads_and_repeated_reviews(self):
        self.records[3].pop('round')
        self.records[1].pop('head_changed_at')
        self.records.append(dict(self.records[4]))
        card = build_scorecards(self.records, now=self.now)['windows']['24h']['providers']['codex']
        self.assertIsNone(card['stuck_prs']['value'])
        self.assertTrue(card['karen_rounds']['partial'])
        self.assertEqual(card['karen_first_pass_pct']['value'], 0)
        self.assertEqual(card['karen_rounds']['value'], 2)

    def test_revert_boundary_and_unobserved_negative(self):
        pr = self.records[2]
        for delta, expected in ((172800, 100), (172801, 0), (-1, 0)):
            pr['reverted_at'] = pr['merged_at'] + delta
            card = build_scorecards(self.records, now=self.now)['windows']['7d']['providers']['codex']
            self.assertEqual(card['rollback_48h_pct']['value'], expected)
        del pr['reverted_at']
        self.assertIsNone(build_scorecards(self.records, now=self.now)['windows']['7d']['providers']['codex']['rollback_48h_pct']['value'])

    def test_idle_clipping_overlap_and_expired_weekly(self):
        self.records[-1]['reset_at'] = self.now
        self.records[-2]['start'] = self.now-90000
        data = build_scorecards(self.records, now=self.now)
        self.assertEqual(data['windows']['24h']['idle_slot_hours']['mini']['value'], 48)
        self.assertIsNone(data['windows']['24h']['providers']['codex']['codex_weekly_pct']['value'])
        self.records.append(dict(kind='idle', host='mini', start=self.now-500, end=self.now, idle_slots=1))
        self.assertIsNone(build_scorecards(self.records, now=self.now)['windows']['24h']['idle_slot_hours']['mini']['value'])

    def test_no_inference_for_review_agreement_and_no_cross_provider_grok(self):
        self.records[6]['adjudicated'] = False
        self.records.append(dict(kind='spend', provider='grok_bot', at=self.now-1, tokens=50, usd=10))
        data = build_scorecards(self.records, now=self.now)
        self.assertIsNone(data['windows']['24h']['providers']['codex']['gemini_agreement_pct']['value'])
        self.assertIsNone(data['windows']['24h']['providers']['grok_bot']['usd']['value'])

    def test_saved_snapshot_allowlist_and_panel_integration(self):
        from board_meta import usage_machines_html
        data = build_scorecards(self.records, now=self.now)
        data['windows']['24h']['repos']['.'.join(('100', '64', '1', '2')) + '/private'] = {'secret':'no'}
        data['windows']['24h']['providers']['codex']['usd']['value'] = 'password=secret'
        clean = normalize_scorecards(data)
        self.assertNotIn('secret', json.dumps(clean))
        self.assertNotIn('.'.join(('100', '64', '1', '2')), json.dumps(clean))
        panel = usage_machines_html({'scorecards':data})
        self.assertIn('Quality &amp; spend scorecards', panel)
        self.assertIn('owner/repo', panel)


class SourceTests(unittest.TestCase):
    def test_existing_logs_no_invented_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'gemini-spend.txt'
            path.write_text('2026-10-16 12:00:00 1 gemini-pro 10 20 $1.25\n2026-10-16 13:00:00 model in=2 out=3 $0.50\n2026-10-16 14:00:00 model\n')
            rows=gemini_records(path)
            self.assertEqual([r['tokens'] for r in rows],[30,5,None])
            self.assertEqual([r['usd'] for r in rows],[1.25,.5,None])
            path=root/'provider-usage.tsv'
            path.write_text('2026-10-16T12:00:00\tjob\tclaude\tmodel\tcheap\n2026-10-16T13:00:00\tjob\tgemini\tmodel\toutput_bytes=0\trefused=true\tusable_output=false\n')
            rows=provider_records(path)
            self.assertIsNone(rows[1]['tokens'])
            self.assertNotIn('usable_output', rows[0])
            self.assertTrue(rows[2]['empty_gemini'])
            self.assertTrue(rows[2]['refused'])

    def test_sessions_only_explicit_per_event_tokens_and_weekly_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); day=root/'2026/10/16'; day.mkdir(parents=True)
            row={'type':'event_msg','timestamp':'2026-10-16T20:00:00Z','payload':{'type':'token_count','info':{'last_token_usage':{'total_tokens':40},'total_token_usage':{'total_tokens':100}},'rate_limits':{'secondary':{'window_minutes':10080,'used_percent':22,'resets_at':2000000000}}}}
            (day/'session.jsonl').write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n')
            (day/'auth.jsonl').write_text('must never be read')
            records=session_records(root)
            self.assertEqual(len([r for r in records if r['kind']=='spend']),1)
            self.assertEqual(records[0]['tokens'],40)
            self.assertIsNone(records[0]['usd'])
            self.assertEqual(records[1]['percent'],22)

    def test_existing_notes_and_explicit_adjudication(self):
        text='## Karen 2026-10-16 12:00 CT\n- #75 abcdefa PASS repo=owner/repo provider=codex round=2\n'
        row=note_records(text)[0]
        self.assertEqual(row['round'],2)
        self.assertIsNotNone(row['at'])
        self.assertEqual(note_records('looks good, probably PASS'),[])
        self.assertEqual(note_records('SCORECARD {"kind":"review","repo":"other/repo"}',repo='owner/repo'),[])

    def test_github_first_head_first_failed_check_and_trusted_comments(self):
        repo='owner/repo'
        endpoints={
            f'repos/{repo}/events?per_page=100':[{'type':'PullRequestEvent','created_at':'2026-10-16T10:00:00Z','payload':{'number':1,'action':'opened','pull_request':{'head':{'sha':'aaa'}}}}],
            f'repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=100':[{'number':1,'state':'open','created_at':'2026-10-16T10:00:00Z','head':{'sha':'aaa','ref':'feature'},'labels':[{'name':'provider:codex'}]}],
            f'repos/{repo}/commits/aaa/check-runs?filter=all&per_page=100':[{'id':2,'name':'test','conclusion':'success'},{'id':1,'name':'test','conclusion':'failure'}],
            f'repos/{repo}/issues/1/comments?per_page=100':[{'user':{'login':'karen'},'created_at':'2026-10-16T11:00:00Z','body':'KAREN pr=1 sha=abcdefa PASS round=1'},{'user':{'login':'other'},'created_at':'2026-10-16T11:00:00Z','body':'KAREN pr=1 sha=abcdefa FAIL'}],
        }
        endpoints[f'repos/{repo}/pulls/1/reviews?per_page=100'] = []
        rows=github_records(repo, {'karen'}, read=endpoints.__getitem__)
        self.assertFalse(rows[0]['first_push_green'])
        self.assertEqual(rows[0]['head_changed_at'],'2026-10-16T10:00:00Z')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['provider'],'codex')
        self.assertEqual(rows[1]['verdict'],'PASS')
