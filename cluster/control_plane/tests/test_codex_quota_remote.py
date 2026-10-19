"""SSH host-bound admission cannot borrow another account's quota."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from cluster.control_plane import codex_quota as q
from cluster.control_plane import codex_quota_remote as remote
from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.tests.test_codex_quota import NOW, event


NONCE='a'*32


def packet(pct=29,age=0,skew=0):
    value=q._observation(NOW+skew-age,event(pct)['payload']['rate_limits'],NOW+skew,q.MAX_AGE_SECONDS)
    return dict(schema=1,limit_id='codex',nonce=NONCE,served_at=NOW+skew,quota=value,
                private='private-account-and-token')


def fake_ssh(tmp_path,mode='ok'):
    executable=tmp_path/'fake-ssh'
    executable.write_text('''#!/usr/bin/python3
import json,sys,time
from pathlib import Path
assert sys.argv[1:5]==['-o','BatchMode=yes','-o','ConnectTimeout=5']
assert sys.argv[5]=='nuc'
command=sys.argv[6]
assert command.startswith('exec "$HOME/.local/bin/storypool-codex-quota-read" --nonce ')
nonce=command.split()[-1]
Path(__file__).with_name('remote-command.json').write_text(json.dumps(sys.argv[1:]))
mode=MODE
value=PACKET
value['nonce']=nonce if mode!='nonce' else 'b'*32
if mode=='timeout':time.sleep(30)
if mode=='failure':sys.exit(255)
if mode=='oversize':print('x'*10000,flush=True);time.sleep(30)
elif mode=='malformed':print('private-not-json',flush=True)
else:print(json.dumps(value),flush=True)
'''.replace('MODE',repr(mode)).replace('PACKET',repr(packet())))
    executable.chmod(0o700)
    return str(executable)


def info():
    return PRInfo('owner','repo',1,'a'*40,'Fixture','author',False,'+safe',{},['file.py'],[])


def final_review():
    return json.dumps(dict(verdict='FAIL',reason='Reproduced file.py:12 functional defect',confidence=.9,
                           findings=dict(functional_issues=['file.py:12 wrong result'])))


@pytest.mark.parametrize('skew',[-9,0,7,3600])
def test_remote_source_clock_plus_round_trip_validates_freshness_without_assuming_clock_equality(skew):
    value=remote.validate_reply(packet(skew=skew),NONCE,2)
    assert value['weekly_pct']==29
    assert 'private' not in json.dumps(value)


@pytest.mark.parametrize('change',['nonce','limit-id','schema','future','stale','window','reset','nonfinite','source','duration'])
def test_stale_future_forged_or_invalid_remote_evidence_cannot_authorize(change):
    value=packet()
    elapsed=1
    if change=='nonce':value['nonce']='b'*32
    elif change=='limit-id':value['limit_id']='other'
    elif change=='schema':value['schema']=True
    elif change=='future':value['quota']['observed_at']=event(age=-1)['timestamp']
    elif change=='stale':value['quota']['observed_at']=event(age=3600)['timestamp']
    elif change=='window':value['quota']['window_minutes']=300
    elif change=='reset':value['quota']['resets_at']=int(NOW-1)
    elif change=='nonfinite':value['quota']['weekly_pct']=float('nan')
    elif change=='source':value['quota']['source']='untrusted'
    else:elapsed=10.01
    assert remote.validate_reply(value,NONCE,elapsed)['weekly_pct'] is None


def test_fixed_ssh_reader_is_bounded_readonly_and_sanitized(tmp_path):
    value=remote.read_remote_codex_quota('nuc',executable=fake_ssh(tmp_path))
    assert value['weekly_pct']==29
    command=json.loads((tmp_path/'remote-command.json').read_text())
    assert len(command)==6
    assert 'BatchMode=yes' in command and 'ConnectTimeout=5' in command
    assert 'codex-quota-read' in command[-1]
    assert 'private' not in json.dumps(value)


@pytest.mark.parametrize('mode',['nonce','failure','malformed','oversize','timeout'])
def test_remote_read_failures_return_unknown_without_error_text_or_inference(tmp_path,mode):
    value=remote.read_remote_codex_quota('nuc',timeout=.3,executable=fake_ssh(tmp_path,mode))
    assert value['weekly_pct'] is None
    assert 'private' not in json.dumps(value)


@pytest.mark.parametrize('host',['-oProxyCommand=bad','nuc; bad','nuc\nother','',None])
def test_invalid_host_cannot_become_ssh_options_or_shell_code(host):
    with patch.object(remote.subprocess,'Popen') as process:
        assert remote.read_remote_codex_quota(host)['weekly_pct'] is None
    process.assert_not_called()


@pytest.mark.parametrize('local,remote_pct,allowed',[(5,80,False),(80,5,True),(5,None,False),(5,75,False)])
def test_review_availability_checks_selected_remote_quota_even_when_local_cli_exists(local,remote_pct,allowed):
    reviewer=r.CodexReviewer()
    with patch.object(reviewer,'_can_ssh_to_host',return_value=True), \
         patch.object(reviewer,'_codex_available_locally',return_value=True), \
         patch.object(r,'read_codex_quota',return_value={'weekly_pct':local}) as local_read, \
         patch.object(r,'read_remote_codex_quota',return_value={'weekly_pct':remote_pct}) as remote_read:
        assert reviewer.is_available(KarenConfig(codex_host='nuc'))[0] is allowed
    local_read.assert_not_called()
    remote_read.assert_called_once()


@pytest.mark.parametrize('local_pct',[None,75,99])
def test_ssh_unavailability_rechecks_local_quota_and_never_dispatches_over_cap(local_pct):
    reviewer=r.CodexReviewer()
    with patch.object(reviewer,'_can_ssh_to_host',return_value=False), \
         patch.object(reviewer,'_codex_available_locally',return_value=True), \
         patch.object(r,'read_codex_quota',return_value={'weekly_pct':local_pct}), \
         patch.object(r,'read_remote_codex_quota') as remote_read, \
         patch.object(r.subprocess,'run') as execute:
        result=reviewer.review(info(),r.ReviewTier.LOW,KarenConfig(codex_host='nuc'))
    assert result.verdict==r.ReviewVerdict.ERROR
    execute.assert_not_called();remote_read.assert_not_called()


def test_available_remote_balance_cannot_authorize_later_local_fallback_at_cap():
    reviewer=r.CodexReviewer()
    with patch.object(reviewer,'_can_ssh_to_host',side_effect=[True,False]), \
         patch.object(reviewer,'_codex_available_locally',return_value=True), \
         patch.object(r,'read_remote_codex_quota',return_value={'weekly_pct':5}), \
         patch.object(r,'read_codex_quota',return_value={'weekly_pct':80}), \
         patch.object(r.subprocess,'run') as execute:
        config=KarenConfig(codex_host='nuc')
        assert reviewer.is_available(config)[0]
        result=reviewer.review(info(),r.ReviewTier.LOW,config)
    assert result.verdict==r.ReviewVerdict.ERROR
    execute.assert_not_called()


def test_low_local_quota_allows_preflight_ssh_fallback_with_final_output_contract():
    reviewer=r.CodexReviewer()
    def execute(args,**kwargs):
        assert args[0]=='codex' and 'TAIL' in kwargs['input']
        Path(args[args.index('-o')+1]).write_text(final_review())
        return subprocess.CompletedProcess(args,0,'private progress','')
    pr=info();pr.diff+='+TAIL'
    with patch.object(reviewer,'_can_ssh_to_host',return_value=False), \
         patch.object(reviewer,'_codex_available_locally',return_value=True), \
         patch.object(r,'read_codex_quota',return_value={'weekly_pct':5}), \
         patch.object(r,'read_remote_codex_quota') as remote_read, \
         patch.object(r.subprocess,'run',side_effect=execute):
        result=reviewer.review(pr,r.ReviewTier.LOW,KarenConfig(codex_host='nuc'))
    assert result.verdict==r.ReviewVerdict.FAIL
    remote_read.assert_not_called()


def test_remote_unknown_does_not_silently_borrow_low_local_balance():
    reviewer=r.CodexReviewer()
    with patch.object(reviewer,'_can_ssh_to_host',return_value=True), \
         patch.object(r,'read_remote_codex_quota',return_value={'weekly_pct':None}), \
         patch.object(r,'read_codex_quota',return_value={'weekly_pct':5}) as local_read, \
         patch.object(r.subprocess,'run') as execute:
        result=reviewer.review(info(),r.ReviewTier.LOW,KarenConfig(codex_host='nuc'))
    assert result.verdict==r.ReviewVerdict.ERROR
    execute.assert_not_called();local_read.assert_not_called()
