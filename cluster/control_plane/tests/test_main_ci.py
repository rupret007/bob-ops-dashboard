"""Main CI follow-up survives process restarts and never reverts the PR head."""
import json
import subprocess
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import conductor as c, main_ci as m
from cluster.control_plane.coordination import CoordinationError
from cluster.control_plane.tests.test_coordination import client

SHA = 'b' * 40
HEAD = 'a' * 40
REPO = 'owner/repo'


@pytest.fixture(autouse=True)
def isolated_budget_admission(monkeypatch):
    """CI semantics are isolated; shared account admission has broker race tests."""
    monkeypatch.setattr(m.github_reads.budget, 'reserve', lambda resource: None)


def details():
    return dict(state='MERGED', mergeCommit={'oid': SHA}, headRefOid=HEAD, baseRefName='main')


def test_pending_main_ci_is_not_green(tmp_path):
    runner = c.Conductor(log_dir=tmp_path, budget_tracker=Mock(), metrics_tracker=Mock())
    with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps({'state':'pending', 'statuses':[{}]}))):
        assert runner._check_main_ci_after_merge(REPO)[0] is False


@pytest.mark.parametrize('conclusion,expected', [('success',(True,'success')), ('failure',(False,'failure')),
    ('timed_out',(False,'failure')), ('cancelled',(False,'failure')), (None,(False,'pending'))])
def test_check_run_ci(conclusion, expected):
    run = dict(id=1, name='tests', app={'id':1}, status='completed' if conclusion else 'in_progress', conclusion=conclusion)
    with patch.object(m, 'gh_json', side_effect=[[{'check_runs':[run]}], {'statuses':[]}]):
        assert m.check_ci(REPO, SHA) == expected


def test_successful_rerun_supersedes_old_failure():
    runs = [dict(id=i, name='test', app={'id':1}, check_suite={'id':99}, status='completed', conclusion=verdict)
            for i,verdict in [(2,'success'),(1,'failure')]]
    with patch.object(m, 'gh_json', side_effect=[[{'check_runs':runs}], {'statuses':[]}]):
        assert m.check_ci(REPO, SHA) == (True,'success')


@pytest.mark.parametrize('include_suite', [True, False])
@pytest.mark.parametrize('blocked', ['failure', None])
def test_same_name_other_workflow_cannot_hide_red_or_pending_ci(include_suite, blocked):
    runs = [dict(id=1, name='test', app={'id':1}, status='completed' if blocked else 'in_progress', conclusion=blocked),
            dict(id=2, name='test', app={'id':1}, status='completed', conclusion='success')]
    if include_suite:
        for run in runs:
            run['check_suite'] = {'id':run['id']}
    with patch.object(m, 'gh_json', side_effect=[[{'check_runs':runs}], {'statuses':[]}]):
        assert m.check_ci(REPO, SHA) == (False, 'failure' if blocked else 'pending')


@pytest.mark.parametrize('response', [([], {'statuses':[]}), ([{'check_runs':[]}], {'statuses':[{}], 'state':'failure'})])
def test_missing_or_red_commit_status_is_never_green(response):
    with patch.object(m, 'gh_json', side_effect=response):
        assert not m.check_ci(REPO, SHA)[0]


def test_delayed_red_rechecks_exact_squash_commit_and_creates_revert():
    lease = Mock()
    with patch.object(m, 'check_ci', side_effect=[(False,'pending'),(False,'failure')]) as check, \
         patch.object(m, 'open_revert') as revert, \
         patch.object(m, 'gh_json', return_value={'sha':SHA}):
        assert m.observe(REPO,7,details(),lease) == 'pending'
        revert.assert_not_called()
        # A later tick has no prior in-memory state.
        assert m.observe(REPO,7,details(),lease) == 'failure'
        assert check.call_args.args == (REPO,SHA)
        revert.assert_called_once_with(REPO,7,SHA,'main',lease)


def test_revert_creates_commit_and_pushes_only_new_branch(tmp_path):
    lease = Mock()
    with patch.object(m, 'gh_json', return_value=[]), patch.object(m, 'command', return_value='url') as command:
        assert m.open_revert(REPO,7,SHA,'main',lease) == 'url'
    commands = [call.args[0] for call in command.call_args_list]
    assert ['git','push','origin','HEAD:refs/heads/storypool-revert-'+SHA] in commands
    revert = next(a for a in commands if 'revert' in a)
    assert revert[-1] == SHA and '--no-edit' in revert
    assert commands[-1][:3] == ['gh','pr','create']
    assert '--label' in commands[-1] and 'needs-karen' in commands[-1]
    assert all('--force' not in a and 'HEAD:main' not in a for a in commands)
    assert lease.renew.call_count >= 4


def test_existing_revert_is_idempotent():
    with patch.object(m, 'gh_json', return_value=[{'url':'existing'}]), patch.object(m, 'command') as command:
        assert m.open_revert(REPO,7,SHA,'main',Mock()) == 'existing'
        command.assert_not_called()


def test_lost_claim_fences_revert_push():
    lease = Mock(); lease.renew.side_effect=[None,None,CoordinationError('lost')]
    with patch.object(m, 'gh_json', return_value=[]), patch.object(m, 'command') as command:
        with pytest.raises(CoordinationError):
            m.open_revert(REPO,7,SHA,'main',lease)
        assert not any('push' in call.args[0] for call in command.call_args_list)


def test_coordination_outage_stops_followup():
    with patch.object(m.ClaimClient, 'from_env', side_effect=CoordinationError('offline')), patch.object(m, 'gh_json') as gh:
        with pytest.raises(CoordinationError):
            m.reconcile_recent()
        gh.assert_not_called()


def test_protected_repo_cannot_get_a_revert():
    with patch.object(m, 'command') as command:
        with pytest.raises(ValueError):
            m.open_revert('rupret007/rad-dad-show-night',7,SHA,'main',Mock())
        command.assert_not_called()


@pytest.mark.parametrize('status', ['failure', 'pending', 'unknown'])
def test_red_main_blocks_live_merge_before_command(client, tmp_path, status):
    runner = c.Conductor(log_dir=tmp_path, budget_tracker=Mock(), metrics_tracker=Mock())
    action = c.ProposedAction('merge_pr',repo=REPO,pr_number=7,head_sha=HEAD)
    runner.validate_action = Mock(return_value=c.ValidatedAction(action,True,'ok'))
    runner._check_main_ci_after_merge = Mock(return_value=(False,status))
    with patch.object(c.ClaimClient,'from_env',return_value=client), patch.object(c.subprocess,'run') as command:
        result=runner.execute_action(c.ValidatedAction(action,True,'ok'),True)
        assert not result.executed and ('main CI ' + status) in result.execution_result
        command.assert_not_called()


def test_restart_recovers_branch_pushed_before_pr_creation():
    calls = []
    def command(argv, cwd=None):
        calls.append(argv)
        if argv[:2] == ['git','push']:
            raise subprocess.CalledProcessError(1, argv)
        if argv[:2] == ['git','rev-parse']:
            return 'same-tree'
        return 'revert-url'
    with patch.object(m,'gh_json',return_value=[]), patch.object(m,'command',side_effect=command):
        assert m.open_revert(REPO,7,SHA,'main',Mock()) == 'revert-url'
    assert any(argv[:2] == ['git','fetch'] for argv in calls)
    assert calls[-1][:3] == ['gh','pr','create']
    assert not any('--force' in argv for argv in calls)


def test_reconcile_after_restart_uses_github_receipts(client):
    response = [{'number':7,'repository':{'nameWithOwner':REPO}}]
    with patch.object(m.ClaimClient,'from_env',return_value=client), \
         patch.object(m,'gh_json',return_value=response), \
         patch.object(m,'merged_pr',return_value=details()), \
         patch.object(m,'observe',return_value='pending') as observe:
        assert m.reconcile_recent() == [{'repo':REPO,'pr':7,'main_ci':'pending'}]
        observe.assert_called_once()
        # A fresh process needs no local merge-state file to retry.
        assert m.reconcile_recent()[0]['main_ci'] == 'pending'
        assert observe.call_count == 2


def test_real_revert_branch_does_not_change_main(tmp_path):
    remote = tmp_path/'remote.git'
    checkout = tmp_path/'source'
    def git(*args, cwd=None):
        return subprocess.check_output(['git',*args],cwd=cwd,text=True,stderr=subprocess.DEVNULL).strip()
    git('init','--bare',str(remote))
    git('init','-b','main',str(checkout))
    git('config','user.name','Test',cwd=checkout)
    git('config','user.email','test@example.invalid',cwd=checkout)
    (checkout/'file').write_text('good')
    git('add','file',cwd=checkout);git('commit','-m','good',cwd=checkout)
    good_tree = git('rev-parse','HEAD^{tree}',cwd=checkout)
    (checkout/'file').write_text('broken')
    git('commit','-am','broken',cwd=checkout)
    bad_sha = git('rev-parse','HEAD',cwd=checkout)
    git('push',str(remote),'main',cwd=checkout)
    real_command = m.command
    def local_command(argv,cwd=None):
        argv = list(argv)
        if argv[:2] == ['git','clone']:
            argv[-2] = str(remote)
        if argv[:3] == ['gh','pr','create']:
            return 'https://example.invalid/revert'
        return real_command(argv,cwd=cwd)
    with patch.object(m,'gh_json',return_value=[]),patch.object(m,'command',side_effect=local_command):
        assert m.open_revert(REPO,7,bad_sha,'main',Mock()).endswith('/revert')
    assert git('--git-dir',str(remote),'rev-parse','refs/heads/main') == bad_sha
    assert git('--git-dir',str(remote),'rev-parse','refs/heads/storypool-revert-'+bad_sha+'^{tree}') == good_tree


def test_two_watchdogs_cannot_duplicate_revert_work(client):
    import concurrent.futures
    import threading
    entered, finish = threading.Event(),threading.Event()
    def observe(*args):
        entered.set()
        assert finish.wait(3)
        return 'failure'
    response = [{'number':7,'repository':{'nameWithOwner':REPO}}]
    with patch.object(m.ClaimClient,'from_env',return_value=client), \
         patch.object(m,'gh_json',return_value=response), \
         patch.object(m,'merged_pr',return_value=details()), \
         patch.object(m,'observe',side_effect=observe) as observer:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first=pool.submit(m.reconcile_recent)
            try:
                assert entered.wait(3)
                second=pool.submit(m.reconcile_recent).result(3)
                assert second[0]['main_ci']=='deferred'
            finally:
                finish.set()
            assert first.result(3)[0]['main_ci']=='failure'
        observer.assert_called_once()


def test_old_failed_merge_is_not_reverted_after_main_recovers():
    with patch.object(m,'check_ci',side_effect=[(False,'failure'),(True,'success')]), \
         patch.object(m,'gh_json',return_value={'sha':'c'*40}), \
         patch.object(m,'open_revert') as revert:
        assert m.observe(REPO,7,details(),Mock()) == 'recovered'
        revert.assert_not_called()


def test_followup_logs_cannot_overwrite_machine_health(tmp_path):
    from cluster.control_plane import watchdog
    health = tmp_path/'watchdog.log'
    with patch.object(watchdog,'LOG_DIR',tmp_path),patch.object(watchdog,'WATCHDOG_LOG',health):
        watchdog._write_watchdog_log({'checks':[{'passed':False}]})
        original=health.read_text()
        watchdog._write_watchdog_log({'action':'main_ci_followup','status':'deferred'},followup=True)
        assert health.read_text()==original
        assert json.loads((tmp_path/'main-ci.jsonl').read_text())['status']=='deferred'
