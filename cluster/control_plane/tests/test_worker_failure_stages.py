"""Trust operation boundaries, preserve failed outcomes, never parse error prose."""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import fix_worker as worker, improvement
from cluster.control_plane.coordination import ClaimClient, ClaimLost, CoordinationError
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.retry_policy import (FAILURE_REASONS, REASONS, STAGE_CODES,
                                               WorkerFailure, classify_failure, reserve)
from cluster.control_plane.service_logging import failure_reason


PRIVATE = 'private-credential-and-prompt-value'
HEAD = 'a'*40
PUSHED = 'b'*40


def execute_fixture(tmp_path, boundary, error=None):
    ledger = JobLedger(tmp_path/'jobs')
    job = ledger.create_job('owner/repo', 7, HEAD, 'codex', 'nucbox')
    lease = MagicMock()
    lease.client.retry_state.return_value = dict(attempts=2, retry_after=12345, code='transient')
    calls, events = [], []
    committed = False

    def command(args, cwd=None, **kwargs):
        nonlocal committed
        calls.append(args)
        at = ('provider' if args[0] == 'codex' else
              'tests' if args == ['npm','test'] else
              'test-setup' if args[:2] == ['npm','ci'] else
              'git' if args[:2] == ['git','clone'] else
              'push' if args[:2] == ['git','push'] else
              'verification' if args[:2] == ['git','ls-remote'] else None)
        if at == boundary and error is not None:
            raise error
        if args[:3] == ['gh','pr','view']:
            return json.dumps(dict(headRefName='fix/one',headRefOid=HEAD,
                                   isCrossRepository=False,state='OPEN'))
        if args[:3] == ['git','worktree','add']:
            tree=Path(args[-2]);tree.mkdir(parents=True)
            (tree/'package.json').write_text(json.dumps({'scripts':{'test':'fixture'}}))
            (tree/'package-lock.json').write_text('{}')
        if args[:3] == ['git','rev-parse','HEAD']:
            return PUSHED if committed else HEAD
        if args[:4] == ['git','diff','--cached','--name-only']:
            return '' if boundary == 'no-change' else 'changed.py'
        if args[:2] == ['git','commit']:
            committed=True
        if args[:2] == ['git','ls-remote']:
            return ('c'*40 if boundary == 'mismatch' else PUSHED)+' refs/heads/fix/one'
        return ''

    if boundary == 'coordination':
        lease.__enter__.side_effect = CoordinationError(PRIVATE)
    with patch.object(worker,'get_ledger',return_value=ledger), \
         patch('cluster.control_plane.node_control.is_draining',return_value=False), \
         patch('cluster.control_plane.coordination.Lease',return_value=lease), \
         patch.object(ClaimClient,'from_env'), \
         patch.object(worker,'model_label',return_value='fixture'), \
         patch.object(worker,'_find_codex',return_value='codex'), \
         patch.object(worker,'findings_for_pr',return_value=[] if boundary=='findings' else ['repair']), \
         patch.object(worker,'run',side_effect=command), \
         patch.object(worker,'event',side_effect=lambda *args,**kwargs:events.append((args,kwargs))):
        result=worker.execute(job.job_id)
    return result, ledger._load_jobs()[0], lease, calls, events


@pytest.mark.parametrize('stage',list(STAGE_CODES))
def test_exception_text_and_argv_never_choose_category_or_enter_reason(stage):
    for error in (RuntimeError('no-change tests-failed '+PRIVATE),
                  subprocess.CalledProcessError(99,[PRIVATE,'push'],output=PRIVATE,stderr=PRIVATE),
                  FileNotFoundError(2,PRIVATE,PRIVATE)):
        code, measured=classify_failure(error,stage)
        assert code == STAGE_CODES[stage] and measured == stage
        assert failure_reason(error,stage=stage) == FAILURE_REASONS[code]
        assert PRIVATE not in failure_reason(error,stage=stage)


@pytest.mark.parametrize('boundary,error,code,stage',[
    ('provider',subprocess.CalledProcessError(1,[PRIVATE],output=PRIVATE,stderr=PRIVATE),'provider-failed','provider'),
    ('provider',RuntimeError('tests-failed no-change '+PRIVATE),'provider-failed','provider'),
    ('tests',subprocess.CalledProcessError(1,[PRIVATE],output=PRIVATE,stderr=PRIVATE),'tests-failed','tests'),
    ('test-setup',RuntimeError(PRIVATE),'test-setup-failed','test-setup'),
    ('git',RuntimeError('provider-failed '+PRIVATE),'git-failed','git'),
    ('push',subprocess.CalledProcessError(128,[PRIVATE],stderr=PRIVATE),'push-failed','push'),
    ('verification',RuntimeError('no-change '+PRIVATE),'verification-failed','verification'),
    ('mismatch',None,'verification-failed','verification'),
    ('provider',subprocess.TimeoutExpired([PRIVATE],1800,output=PRIVATE),'deadline','provider'),
    ('provider',ClaimLost(PRIVATE),'ownership-lost','provider'),
    ('coordination',None,'coordination-failed','coordination'),
    ('findings',None,'findings-unavailable','findings'),
])
def test_future_jobs_keep_trusted_code_and_stage_without_inventing_success(tmp_path,boundary,error,code,stage):
    result,job,lease,calls,events=execute_fixture(tmp_path,boundary,error)
    assert result==1 and job.state=='failed'
    assert job.failure_code==code and job.failure_stage==stage
    assert job.error==FAILURE_REASONS[code]
    assert job.retryable is True
    assert job.outcome_verified is False and job.outcome_sha=='' and job.outcome==''
    assert PRIVATE not in json.dumps(job.to_dict())+str(events)
    terminal=next(fields for args,fields in events if args[1]=='job_failed')
    assert terminal['failure_code']==code and terminal['failure_stage']==stage
    if boundary!='coordination':
        state=lease.client.retry_state.call_args.args[1]
        assert state==dict(attempts=2,retry_after=12345,code=code)
    if boundary=='tests':
        assert sum(args==['npm','test'] for args in calls)==worker.MAX_FIX_ATTEMPTS
        assert not any(args[:2] in (['git','commit'],['git','push']) for args in calls)
    assert worker._failure_stage.get()=='supervisor'


def test_successful_provider_and_tests_with_empty_diff_is_failed_no_change_not_transient(tmp_path):
    result,job,lease,calls,events=execute_fixture(tmp_path,'no-change')
    assert result==1 and job.state=='failed' and job.failure_code=='no-change'
    assert job.failure_stage=='verification' and job.error==FAILURE_REASONS['no-change']
    assert sum(args[0]=='codex' for args in calls)==1
    assert sum(args==['npm','test'] for args in calls)==1
    assert not any(args[:2] in (['git','commit'],['git','push']) for args in calls)
    assert job.retryable is True and not job.outcome_verified
    assert 'no-change' not in REASONS
    # Classifying the first no-change does not park it or reset its exact-head budget.
    state=lease.client.retry_state.call_args.args[1]
    next_attempt,reason=reserve(state,now=state['retry_after'])
    assert not reason and next_attempt['attempts']==3
    assert reserve(next_attempt,now=next_attempt['retry_after'])[0] is None


def test_unknown_stage_and_invalid_typed_categories_cannot_copy_arbitrary_values():
    assert classify_failure(RuntimeError(PRIVATE),PRIVATE)==('worker-failed','supervisor')
    assert classify_failure(RuntimeError(PRIVATE),{})==('worker-failed','supervisor')
    with pytest.raises(ValueError,match='Unknown controlled worker failure'):
        WorkerFailure(PRIVATE,'provider')


def test_actual_command_failure_logs_controlled_stage_not_captured_output(tmp_path,monkeypatch):
    monkeypatch.setenv('STORYPOOL_LOG_DIR',str(tmp_path))
    token=worker._failure_stage.set('provider')
    try:
        error=subprocess.CalledProcessError(1,['codex',PRIVATE],output=PRIVATE,stderr=PRIVATE)
        with patch.object(worker.subprocess,'run',side_effect=error):
            with pytest.raises(subprocess.CalledProcessError):
                worker.run(['codex','exec','-'],input=PRIVATE)
    finally:
        worker._failure_stage.reset(token)
    raw=(tmp_path/'fix-worker.jsonl').read_text()
    assert PRIVATE not in raw
    rows=[json.loads(line) for line in raw.splitlines()]
    assert rows[-1]['failure_stage']=='provider'
    assert rows[-1]['failure_code']=='provider-failed'
    assert rows[-1]['reason']==FAILURE_REASONS['provider-failed']


def test_historical_unknowns_stay_unknown_and_all_failed_expired_jobs_remain_in_denominator():
    now=time.time();stamp=datetime.fromtimestamp(now-1,timezone.utc).isoformat()
    rows=[dict(job_id='verified',state='done',outcome_verified=True,outcome='commit_pushed',
               outcome_sha=PUSHED,end_time=stamp)]
    for number,code in enumerate(['no-change','provider-failed','','transient']):
        rows.append(dict(job_id=str(number),state='failed',failure_code=code,
                         error='No fix produced '+PRIVATE,end_time=stamp))
    rows.append(dict(job_id='expired',state='expired',end_time=stamp))
    before=json.dumps(rows,sort_keys=True)
    measured=improvement.measure(rows,[],now)
    assert measured['terminal_jobs']==6 and measured['verified_jobs']==1
    assert measured['verified_job_percent']==16.67
    counts={row['code']:row['count'] for row in measured['top_failures']}
    assert counts=={'no-change':1,'provider-failed':1,'unclassified':2,'transient':1}
    assert before==json.dumps(rows,sort_keys=True)
    assert PRIVATE not in json.dumps(measured)


@pytest.mark.parametrize('running',[True,False])
def test_new_lifecycle_failure_is_classified_without_changing_historical_unknowns(tmp_path,running):
    from cluster.control_plane.job_ledger import Job
    ledger=JobLedger(tmp_path/'jobs')
    old=ledger.create_job('owner/old',1,HEAD,'codex','nucbox')
    ledger.fail_job(old.job_id,'Old unknown failure')
    new=ledger.create_job('owner/new',2,HEAD,'codex','nucbox')
    if running:
        ledger.start_job(new.job_id,12345)
    with patch.object(Job,'is_process_running',return_value=False), \
         patch.object(Job,'has_valid_lease',return_value=False):
        changed=ledger.expire_stale_leases()
    assert len(changed)==1
    assert changed[0].failure_code==('worker-died' if running else 'lease-expired')
    assert changed[0].failure_stage==('supervisor' if running else 'coordination')
    saved={row.job_id:row for row in ledger._load_jobs()}
    assert saved[old.job_id].failure_code=='' and saved[old.job_id].failure_stage==''
    assert saved[old.job_id].error=='Old unknown failure'
