"""Scheduled observation cannot consume the gate's whole account allowance."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import backlog, github_reads, github_budget as budget
from cluster.control_plane.local_karen import poller, review_queue as q, github_client
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.repo_scan import RepoScan, RepoScanError


def candidates(repo, count=1):
    return [q.Candidate(repo, dict(number=n, headRefOid=f'{n:040x}', isDraft=False)) for n in range(1, count+1)]


@pytest.mark.parametrize('operation', [backlog.discover_repos, lambda: backlog.get_open_prs('owner/repo'),
    lambda: backlog.collect_backlog(['owner/repo']), lambda: backlog.collect_karen_failures(['owner/repo'])])
def test_backlog_budget_unknown_never_becomes_empty(operation):
    error = budget.GitHubDeferred('bounded admission deferral', code='minute-exhausted', retry_after=12)
    with patch.object(github_reads, 'run', side_effect=error), \
         patch.object(github_reads, 'active_repos', side_effect=error):
        with pytest.raises(budget.GitHubDeferred) as raised:
            operation()
    assert raised.value is error and raised.value.retry_after == 12


def test_client_preserves_typed_deferral_and_gate_still_fails_closed(tmp_path):
    from cluster.control_plane.local_karen.gate import GateRunner
    error = budget.GitHubDeferred('budget deferred', code='minute-exhausted', retry_after=3)
    with patch.object(github_client, 'budgeted_gh', side_effect=error):
        with pytest.raises(budget.GitHubDeferred) as raised:
            github_client._run_gh(['pr', 'view', '1'])
        assert raised.value is error
        runner = GateRunner(KarenConfig(state_dir=str(tmp_path)))
        with patch('cluster.control_plane.local_karen.gate.run_review_chain') as provider:
            result = runner.run('owner', 'repo', 1, expected_sha='a'*40)
        provider.assert_not_called()
    assert result.error and result.verdict_path is None and not result.sha_verified
    assert not result.verdict.passed and result.verdict.verdict_type == 'ERROR'


def test_rotation_covers_all_repos_without_claiming_uncompleted_reads(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    repos = [f'owner/repo{n:02}' for n in range(18)]
    seen = []
    for _ in range(5):
        scan = RepoScan(config, repos)
        assert len(scan.repos) == 4
        for repo in scan.repos:
            scan.observed(repo); seen.append(repo)
    assert seen[:18] == repos and seen[18:] == repos[:2]
    scan = RepoScan(config, repos)
    uncompleted = scan.repos[1]
    scan.observed(scan.repos[0])
    assert RepoScan(config, repos).repos[0] == uncompleted
    assert scan.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('value', ['not JSON', '{}', '{"version":true,"last_repo":"owner/repo"}',
    '{"version":1,"last_repo":"../private"}', 'x'*4097])
def test_malformed_cursor_fails_closed(tmp_path, value):
    (tmp_path/'review-repo-scan.json').write_text(value)
    with pytest.raises(RepoScanError):
        RepoScan(KarenConfig(state_dir=str(tmp_path)), ['owner/repo'])


def test_cursor_write_failure_cannot_start_gate(tmp_path):
    scan = RepoScan(KarenConfig(state_dir=str(tmp_path)), ['owner/repo'])
    with patch.object(poller, 'list_prs_with_label', return_value=[candidates('owner/repo')[0].pr]), \
         patch('cluster.control_plane.local_karen.repo_scan.os.replace', side_effect=OSError()), \
         patch.object(poller, 'run_gate') as gate:
        with pytest.raises(RepoScanError):
            poller.poll_and_run(scan.repos, KarenConfig(state_dir=str(tmp_path)), scan=scan)
    gate.assert_not_called()


def test_partial_scan_preserves_unseen_aging_reconciles_only_complete_repo_heads(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    state = q.ReviewQueue(config)
    unseen, old = candidates('owner/unseen')[0], candidates('owner/scanned')[0]
    state.deferred = {unseen.key: 7, old.key: 4}; state.save()
    changed = candidates('owner/scanned', 2)[1]
    state.begin([changed], observed_repos={'owner/scanned'})
    restored = q.ReviewQueue(config)
    assert restored.deferred == {unseen.key: 7, changed.key: 1}
    restored.begin([], observed_repos={'owner/unseen'})
    assert restored.deferred == {changed.key: 1}


def test_persisted_aging_is_bounded_without_overwriting_previous_file(tmp_path):
    state = q.ReviewQueue(KarenConfig(state_dir=str(tmp_path)))
    state.deferred = {'valid': 1}; state.save(); before = state.path.read_bytes()
    state.deferred = {str(n): 1 for n in range(10001)}
    with pytest.raises(OSError): state.save()
    assert state.path.read_bytes() == before


def test_one_metadata_batch_leaves_others_unknown_and_ages_them_for_next_turn(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    items = candidates('owner/repo', 60)
    with patch.object(q.github_reads, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as read:
        q.enrich(items, max_batches=1)
    assert read.call_count == 1 and all(item.ci == 'unknown' for item in items)
    state = q.ReviewQueue(config)
    for _ in range(3):
        ordered = state.order(items); state.begin(items, observed_repos={'owner/repo'})
        for item in ordered[:q.MAX_REVIEWS_PER_CYCLE]: state.attempted(item)
    assert state.order(items)[0] not in items[:q.MAX_REVIEWS_PER_CYCLE]


class FakeAccount:
    """Use the real rolling-ledger mutation; only CLI/network are replaced."""
    def __init__(self):
        self.now = 100000.
        self.state = {'version': 1, 'requests': [], 'resources': {}}
        for _ in range(24): self.reserve('core', background=True)
        self.starts = []
        self.ready = []

    def reserve(self, resource='core', *, background=False):
        budget._mutate(self.state, 'reserve', resource, {}, False, self.now, background=background)

    def cli(self, args, **kwargs):
        assert args[0] == 'gh'
        if 'graphql' in args:
            query = args[-1]
            values = {name: {'pullRequest': {'headRefOid': f'{int(number):040x}', 'additions': 4, 'deletions': 0,
                'commits': {'nodes': [{'commit': {'statusCheckRollup': {'state': 'SUCCESS'}}}]}}}
                for name, number in re.findall(r'(r\d+):repository\(owner:"[^"]+",name:"[^"]+"\)\{pullRequest\(number:(\d+)\)', query)}
            value = {'data': values}
        elif 'rate_limit' in args:
            value = {'resources': {}}
        else:
            value = [dict(number=1, title='Fixture', isDraft=False, draft=False, headRefOid=f'{1:040x}',
                head={'sha': f'{1:040x}'}, labels=[{'name': 'needs-karen'}], author={}, user={})]
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')

    def gate(self, **kwargs):
        self.starts.append(kwargs)
        for _ in range(5): self.reserve('core')
        self.ready.append(kwargs)
        # No provider, artifact, authority or fake approval is manufactured.
        return SimpleNamespace(error='offline provider intentionally absent', sha_verified=False, verdict_path=None)

    def patches(self, tmp_path):
        stack = ExitStack()
        stack.enter_context(patch.dict('os.environ', {'STORYPOOL_DATA_DIR': str(tmp_path/'data'), 'STORYPOOL_MACHINE': 'mini'}))
        stack.enter_context(patch.object(github_reads.budget, 'reserve', side_effect=self.reserve))
        stack.enter_context(patch.object(github_reads.budget, 'observe'))
        stack.enter_context(patch.object(github_reads.subprocess, 'run', side_effect=self.cli))
        stack.enter_context(patch.object(poller, 'run_gate', side_effect=self.gate))
        stack.enter_context(patch.object(poller, 'record_runtime'))
        stack.enter_context(patch('cluster.control_plane.guards.MachineGuards.check_all', return_value=SimpleNamespace(passed=True)))
        stack.enter_context(patch('cluster.control_plane.node_control.is_draining', return_value=False))
        return stack


def test_real_40_cap_reproduces_whole_portfolio_starvation_without_any_review(tmp_path):
    account = FakeAccount(); config = KarenConfig(state_dir=str(tmp_path/'state'))
    with account.patches(tmp_path), pytest.raises(budget.GitHubDeferred):
        poller.poll_and_run([f'owner/repo{n:02}' for n in range(18)], config)
    assert len(account.state['requests']) == 40 and not account.starts


def test_scheduled_rotation_starts_exact_head_checks_under_same_40_cap_and_preserves_report(tmp_path):
    account = FakeAccount()
    config = KarenConfig(state_dir=str(tmp_path/'state'), repos=[f'owner/repo{n:02}' for n in range(18)])
    previous = tmp_path/'backlog.md'; previous.write_text('Previously verified full report')
    with account.patches(tmp_path), patch.object(poller, 'write_backlog_report') as write, \
         patch.object(poller, 'undraft_green_prs') as undraft, patch.object(poller, 'label_queued_prs') as label:
        assert poller.run_poll_once(config, auto_discover=False, auto_label=True, auto_update=False) == []
    assert account.ready and all(call['expected_sha'] == f'{1:040x}' for call in account.ready)
    assert len(account.state['requests']) == 40
    assert len(account.state['foreground_requests']) == 16
    assert RepoScan(config, config.get_repos_list()).repos[0] == 'owner/repo04'
    write.assert_not_called(); undraft.assert_not_called(); label.assert_not_called()
    assert previous.read_text() == 'Previously verified full report'


@pytest.mark.parametrize('phase', ['discovery', 'quota', 'listing', 'enrichment', 'undraft', 'label', 'backlog'])
def test_native_deferral_stops_later_optional_work_and_never_overwrites_unknown_report(tmp_path, phase):
    config = KarenConfig(state_dir=str(tmp_path/'state'), repos=['owner/repo'])
    error = budget.GitHubDeferred('bounded deferral', code='minute-exhausted', retry_after=2)
    previous = tmp_path/'backlog.md'; previous.write_text('last complete report')
    with ExitStack() as stack:
        stack.enter_context(patch('cluster.control_plane.guards.MachineGuards.check_all', return_value=SimpleNamespace(passed=True)))
        stack.enter_context(patch.object(poller, 'record_runtime'))
        stack.enter_context(patch.object(poller.KarenConfig, 'migrate_config'))
        discover = stack.enter_context(patch.object(poller, 'discover_repos', return_value=['owner/repo']))
        quota = stack.enter_context(patch.object(github_reads, 'refresh_quota'))
        listing = stack.enter_context(patch.object(poller, 'list_prs_with_label', return_value=[]))
        enrich = stack.enter_context(patch.object(poller, 'enrich'))
        undraft = stack.enter_context(patch.object(poller, 'undraft_green_prs', return_value=[]))
        label = stack.enter_context(patch.object(poller, 'label_queued_prs', return_value=[]))
        collect = stack.enter_context(patch.object(poller, 'collect_backlog', side_effect=error))
        push = stack.enter_context(patch('cluster.control_plane.local_karen.verdict_push.push'))
        write = stack.enter_context(patch.object(poller, 'write_backlog_report'))
        failures = stack.enter_context(patch.object(poller, 'write_karen_fails_report'))
        selected = {'discovery':discover,'quota':quota,'listing':listing,'enrichment':enrich,'undraft':undraft,'label':label,'backlog':collect}
        selected[phase].side_effect = error
        if phase == 'discovery': config.repos = 'auto'
        assert poller.run_poll_once(config, auto_update=False) == []
    write.assert_not_called(); failures.assert_not_called()
    if phase not in ('label','backlog'): label.assert_not_called()
    if phase not in ('undraft','label','backlog'): undraft.assert_not_called()
    assert previous.read_text() == 'last complete report'


@pytest.mark.parametrize('failure', [SimpleNamespace(returncode=1, stdout='', stderr='private stderr'),
    SimpleNamespace(returncode=0, stdout='not JSON', stderr=''),
    SimpleNamespace(returncode=0, stdout='{}', stderr=''),
    SimpleNamespace(returncode=0, stdout='["malformed PR"]', stderr='')])
def test_failed_backlog_reads_cannot_publish_fresh_empty_report(tmp_path, failure):
    report = tmp_path/'backlog.md'; report.write_text('last complete backlog')
    with patch.object(backlog.github_reads, 'run', return_value=failure), patch.object(backlog, 'BACKLOG_PATH', report):
        with pytest.raises(github_reads.GitHubReadError):
            backlog.write_backlog_report(backlog.collect_backlog(['owner/repo']))
    assert report.read_text() == 'last complete backlog'


def test_failure_report_does_not_replace_previous_content_on_budget_deferral(tmp_path):
    report = tmp_path/'failures.md'; report.write_text('last complete failure report')
    with patch.object(backlog, 'discover_repos', return_value=['owner/repo']), \
         patch.object(backlog.github_reads, 'run', side_effect=budget.GitHubDeferred('bounded deferral')):
        with pytest.raises(budget.GitHubDeferred):
            backlog.write_karen_fails_report(output_path=report)
    assert report.read_text() == 'last complete failure report'


def test_scheduled_metadata_selects_aged_unsampled_candidates_first(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    items = candidates('owner/repo', 60)
    state = q.ReviewQueue(config); state.deferred = {items[-1].key: 7}; state.save()
    scan = RepoScan(config, ['owner/repo'])
    observed = []
    def enrich(selected, max_batches):
        observed.extend(selected[:25]); assert max_batches == 1
    with patch.object(poller, 'list_prs_with_label', return_value=[item.pr for item in items]), \
         patch.object(poller, 'enrich', side_effect=enrich), \
         patch('cluster.control_plane.node_control.is_draining', return_value=True), \
         patch.object(poller, 'run_gate') as gate:
        poller.poll_and_run(scan.repos, config, scan=scan)
    gate.assert_not_called()
    assert observed[0].key == items[-1].key
    assert len(observed) == 25 and all(item.ci == 'unknown' for item in items)


def test_verified_empty_data_is_distinct_from_unavailable_data():
    with patch.object(backlog.github_reads, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]', stderr='')):
        assert backlog.collect_backlog(['owner/repo'])['summary']['total_prs'] == 0


@pytest.mark.parametrize('error', [budget.GitHubDeferred('bounded deferral'), github_reads.GitHubReadError('read unavailable')])
def test_completed_review_survives_optional_deferral_in_history_and_cli(tmp_path, capsys, error):
    config = KarenConfig(state_dir=str(tmp_path/'state'), repos=['owner/repo'])
    result = dict(repo='owner/repo', pr=1, sha='a'*40, passed=True, reason='Synthetic completed result')
    old = dict(repo='owner/other', pr=2, sha='b'*40, passed=False, reason='Older result')
    poller.save_poll_state(poller.PollState('earlier', [old]), config)
    with patch('sys.argv', ['karen-poller', '--once', '--no-update']), \
         patch.object(poller.KarenConfig, 'load', return_value=config), \
         patch.object(poller.KarenConfig, 'migrate_config'), \
         patch.object(poller, 'record_runtime'), \
         patch('cluster.control_plane.guards.MachineGuards.check_all', return_value=SimpleNamespace(passed=True)), \
         patch.object(github_reads, 'refresh_quota'), patch.object(poller, 'poll_and_run', return_value=[result]), \
         patch.object(poller, 'undraft_green_prs', side_effect=error), \
         patch.object(poller, 'label_queued_prs') as label, patch.object(poller, 'write_backlog_report') as report:
        assert poller.main() == 0
    saved = poller.load_poll_state(config)
    assert saved.prs_checked == [old, result] and saved.last_poll != 'earlier'
    assert 'PASS owner/repo#1@' + 'a'*40 in capsys.readouterr().out
    label.assert_not_called(); report.assert_not_called()


@pytest.mark.parametrize('when', ['artifact_label', 'next_admission'])
def test_real_completed_result_is_retained_when_later_admission_work_defers(tmp_path, when):
    config = KarenConfig(state_dir=str(tmp_path/'state'), repos=['owner/repo'])
    items = candidates('owner/repo', 2)
    verdict = SimpleNamespace(head_sha=items[0].pr['headRefOid'], passed=True, reason='Synthetic verified result')
    completed = SimpleNamespace(error=None, sha_verified=True, verdict_path='private-test-only', verdict=verdict)
    error = budget.GitHubDeferred('bounded deferral')
    gate_calls = [completed, error]
    with patch.object(poller, 'record_runtime'), patch.object(poller.KarenConfig, 'migrate_config'), \
         patch('cluster.control_plane.guards.MachineGuards.check_all', return_value=SimpleNamespace(passed=True)), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(github_reads, 'refresh_quota'), \
         patch.object(poller, 'list_prs_with_label', return_value=[item.pr for item in items]), \
         patch.object(poller, 'enrich'), patch.object(poller, 'run_gate', side_effect=gate_calls), \
         patch.object(poller, 'remove_label', side_effect=error) as labels, \
         patch('cluster.control_plane.local_karen.verdict_push.push'), \
         patch.object(poller, 'undraft_green_prs') as undraft, patch.object(poller, 'write_backlog_report') as report:
        results = poller.run_poll_once(config, auto_update=False, remove_label_on_complete=when == 'artifact_label')
    assert len(results) == 1 and results[0]['sha'] == verdict.head_sha and results[0]['passed']
    assert poller.load_poll_state(config).prs_checked == results
    if when == 'next_admission': labels.assert_not_called()
    undraft.assert_not_called(); report.assert_not_called()
