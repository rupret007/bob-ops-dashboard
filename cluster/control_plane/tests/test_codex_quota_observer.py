"""Readonly RPC, bounded ownership, sanitization, and real quota admission."""
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import codex_quota as q
from cluster.control_plane import codex_quota_install as install
from cluster.control_plane import codex_quota_observer as observer
from cluster.control_plane.tests.test_codex_quota import NOW, event, write


def body(pct=29, *, window='primary', **changes):
    bucket = dict(limitId='codex', planType='pro', primary=None, secondary=None)
    bucket[window] = dict(usedPercent=pct, windowDurationMins=10080, resetsAt=int(NOW+86400))
    result = dict(rateLimits=bucket, rateLimitsByLimitId={'codex': bucket},
                  accountId='private-account-id', credits={'balance': 'private-credit'},
                  private='private-token-content')
    result.update(changes)
    return result


def cache(path, pct=29, age=0, **changes):
    value = dict(schema=1, source=q.APP_SOURCE, status='ok',
                 observed_at=event(age=age)['timestamp'], limits=q.normalize_app_limits(body(pct)))
    value.update(changes)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def fake_codex(tmp_path, mode='ok', response=None):
    path = tmp_path / 'fake-codex'
    path.write_text('''#!/usr/bin/python3
import json,os,signal,sys,time
from pathlib import Path
folder=Path(__file__).parent
mode=MODE
response=RESPONSE
requests=[]
def read():
    value=json.loads(sys.stdin.readline()); requests.append(value)
    (folder/'requests.json').write_text(json.dumps(requests))
    return value
def emit(value):
    print(json.dumps(value),flush=True)
assert sys.argv[1:]==['app-server','--stdio','-c','analytics.enabled=false']
assert read()['method']=='initialize'
if mode=='premature':emit({'id':2,'result':response});time.sleep(3);sys.exit()
emit({'id':1,'result':{}})
assert read()['method']=='initialized'
assert read()['method']=='account/rateLimits/read'
if mode in ('timeout','child'):
    child=os.fork()
    if child==0:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        (folder/'child.pid').write_text(str(os.getpid()))
        time.sleep(30);os._exit(0)
    for _ in range(100):
        if (folder/'child.pid').exists():break
        time.sleep(.001)
    if mode=='timeout':
        signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)
if mode=='unknown':emit({'id':999,'result':response})
elif mode=='request':emit({'id':99,'method':'account/chatgptAuthTokens/refresh','params':{'private':'secret'}})
elif mode=='error':emit({'id':2,'error':{'code':-32000,'message':'private-token-content'}})
elif mode=='bad-json':print('{malformed private-token-content}',flush=True)
elif mode=='duplicate':print('{"id":2,"id":2,"result":{}}',flush=True)
elif mode=='oversize':print('x'*300000,flush=True)
elif mode=='messages':
    for _ in range(140):emit({'method':'warning','params':{'private':'private-token-content'}})
else:
    emit({'method':'warning','params':{'private':'private-token-content'}})
    emit({'id':2,'result':response})
'''.replace('MODE', repr(mode)).replace('RESPONSE', repr(body() if response is None else response)))
    path.chmod(0o700)
    return str(path)


def read_both(tmp_path, path):
    return q.read_codex_quota(tmp_path/'sessions', cache_path=path, now=NOW)


@pytest.mark.parametrize('window', ['primary', 'secondary'])
def test_protocol_success_is_only_three_readonly_requests_and_private_fields_are_discarded(tmp_path, monkeypatch, window):
    executable = fake_codex(tmp_path, response=body(window=window))
    path = tmp_path/'quota'/'codex.json'
    monkeypatch.setattr(observer.time, 'time', lambda: NOW)
    result = observer.observe_once(path, executable=executable)
    assert result['weekly_pct'] == 29 and result['source'] == q.APP_SOURCE
    assert result['window'] == window
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert 'private' not in path.read_text()+json.dumps(result)
    assert read_both(tmp_path, path)['weekly_pct'] == 29
    requests = json.loads((tmp_path/'requests.json').read_text())
    assert [row['method'] for row in requests] == list(observer.METHODS)


@pytest.mark.parametrize('mode,reason', [
    ('premature','unexpected_response'), ('unknown','unexpected_response'),
    ('request','server_request_denied'), ('error','rpc_error'),
    ('bad-json','malformed_protocol'), ('duplicate','malformed_protocol'),
    ('oversize','output_limit'), ('messages','message_limit'),
])
def test_unexpected_server_content_and_requests_never_trigger_auth_tools_or_inference(tmp_path, mode, reason):
    with pytest.raises(observer.ObservationError, match=reason):
        observer.fetch_limits(executable=fake_codex(tmp_path, mode), timeout=2)
    methods=[row['method'] for row in json.loads((tmp_path/'requests.json').read_text())]
    assert set(methods) <= set(observer.METHODS)


@pytest.mark.parametrize('mode', ['timeout', 'child'])
def test_timeout_and_successful_parent_exit_reap_only_the_owned_process_group(tmp_path, mode):
    executable=fake_codex(tmp_path, mode)
    started=time.monotonic()
    if mode=='timeout':
        with pytest.raises(observer.ObservationError, match='timeout'):
            observer.fetch_limits(executable=executable, timeout=.3)
    else:
        assert observer.fetch_limits(executable=executable, timeout=2)['rateLimits']['limitId']=='codex'
    assert time.monotonic()-started < 3
    child=int((tmp_path/'child.pid').read_text())
    for _ in range(100):
        state=subprocess.run(['/bin/ps','-p',str(child),'-o','stat='], capture_output=True,text=True).stdout.strip()
        if not state or state.startswith('Z'):break
        time.sleep(.01)
    assert not state or state.startswith('Z')


@pytest.mark.parametrize('pct,allowed', [(74.99,True),(75,False),(100,False)])
def test_fresh_actual_server_observation_controls_existing_budget_cutoff(tmp_path, monkeypatch, pct, allowed):
    from cluster.control_plane.budget_tracker import BudgetTracker, BudgetCaps
    path=cache(tmp_path/'quota'/'codex.json', pct=pct)
    quota=read_both(tmp_path,path)
    monkeypatch.setattr('cluster.control_plane.budget_tracker.read_codex_quota',lambda:quota)
    assert BudgetTracker(tmp_path,BudgetCaps(codex_weekly_pct=90)).can_use('codex')[0] is allowed


def test_newest_timestamp_wins_and_stale_server_cannot_override_a_newer_session_at_cap(tmp_path):
    path=cache(tmp_path/'quota'/'codex.json',29,age=1)
    write(tmp_path/'sessions',[event(80)])
    assert read_both(tmp_path,path)['weekly_pct']==80
    write(tmp_path/'sessions',[event(25,age=10)])
    assert read_both(tmp_path,path)['weekly_pct']==29


def test_equal_timestamp_disagreement_and_future_cache_block_older_lower_usage(tmp_path):
    path=cache(tmp_path/'quota'/'codex.json',29)
    write(tmp_path/'sessions',[event(80)])
    assert read_both(tmp_path,path)['weekly_pct'] is None
    cache(path,29,age=-1)
    assert read_both(tmp_path,path)['status']=='malformed'


@pytest.mark.parametrize('alter', ['missing','wrong-id','conflict','bad-window','nonfinite','private-other-window'])
def test_malformed_new_authoritative_reply_revokes_old_success_without_raw_data(tmp_path, monkeypatch, alter):
    path=cache(tmp_path/'quota'/'codex.json',5,age=5)
    value=body()
    if alter=='missing':value['rateLimitsByLimitId']={}
    elif alter=='wrong-id':value['rateLimits']['limitId']='other'
    elif alter=='conflict':value['rateLimits']=dict(value['rateLimits'],primary=dict(usedPercent=80,windowDurationMins=10080,resetsAt=int(NOW+86400)))
    elif alter=='bad-window':value['rateLimits']['primary']['windowDurationMins']=300
    elif alter=='nonfinite':value['rateLimits']['primary']['usedPercent']=float('nan')
    else:value['rateLimits']['secondary']=dict(usedPercent='private-other-window',windowDurationMins=300,resetsAt=int(NOW+86400))
    monkeypatch.setattr(observer,'fetch_limits',lambda **kwargs:value)
    monkeypatch.setattr(observer.time,'time',lambda:NOW)
    assert observer.observe_once(path)['status']=='malformed'
    assert read_both(tmp_path,path)['weekly_pct'] is None
    assert 'private' not in path.read_text()


def test_transport_failure_preserves_original_observation_age_and_does_not_revive_expired_cache(tmp_path,monkeypatch):
    path=cache(tmp_path/'quota'/'codex.json',29,age=3601)
    before=path.read_bytes()
    def fail(**kwargs):raise observer.ObservationError('timeout')
    monkeypatch.setattr(observer,'fetch_limits',fail)
    assert observer.observe_once(path)==dict(status='error',source=q.APP_SOURCE,reason='timeout')
    assert path.read_bytes()==before
    assert read_both(tmp_path,path)['status']=='stale'


def test_malformed_protocol_replaces_success_with_unknown_without_copying_error_body(tmp_path,monkeypatch):
    path=cache(tmp_path/'quota'/'codex.json',29,age=2)
    result=observer.observe_once(path,executable=fake_codex(tmp_path,'error'))
    assert result['status']=='malformed'
    assert 'private' not in path.read_text()+json.dumps(result)
    assert read_both(tmp_path,path)['weekly_pct'] is None


def test_reached_limit_and_denied_usage_never_authorize_below_75(tmp_path):
    for change in [dict(ordinaryUsageAllowed=False),dict(rateLimitReachedType='limit-state')]:
        value=body()
        if 'ordinaryUsageAllowed' in change:value.update(change)
        else:value['rateLimits'].update(change)
        path=cache(tmp_path/'quota'/'codex.json',limits=q.normalize_app_limits(value))
        assert read_both(tmp_path,path)['status']=='blocked'
        assert read_both(tmp_path,path)['weekly_pct'] is None


@pytest.mark.parametrize('kind',['permissions','symlink','large','invalid-json','duplicate'])
def test_unsafe_or_malformed_cache_fails_closed_without_falling_back(tmp_path,kind):
    path=cache(tmp_path/'quota'/'codex.json')
    write(tmp_path/'sessions',[event(5)])
    if kind=='permissions':path.chmod(0o644)
    elif kind=='symlink':path.rename(path.with_name('target'));path.symlink_to(path.with_name('target'))
    elif kind=='large':path.write_bytes(b' '* (q.CACHE_MAX_BYTES+1))
    elif kind=='duplicate':path.write_text(path.read_text().replace('"schema": 1','"schema": 1, "schema": 1'))
    else:path.write_text('private-invalid-json')
    assert read_both(tmp_path,path)['weekly_pct'] is None
    assert 'private' not in json.dumps(read_both(tmp_path,path))


def test_observer_lock_blocks_duplicate_probes_without_starting_process(tmp_path,monkeypatch):
    folder=tmp_path/'quota';folder.mkdir(mode=0o700)
    with (folder/'observer.lock').open('w') as lock:
        (folder/'observer.lock').chmod(0o600)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with patch.object(observer,'fetch_limits') as fetch:
            assert observer.observe_once(folder/'codex.json')['status']=='busy'
        fetch.assert_not_called()


def test_explicit_real_session_root_uses_cache_and_no_reader_starts_process(tmp_path,monkeypatch):
    monkeypatch.setattr(q.Path,'home',lambda:tmp_path)
    cache(q.quota_cache_path())
    root=tmp_path/'.codex/sessions';root.mkdir(parents=True)
    with patch.object(observer,'fetch_limits') as fetch,patch('subprocess.Popen') as process:
        assert q.read_codex_quota(root,now=NOW)['weekly_pct']==29
        assert q.read_codex_quota(now=NOW)['weekly_pct']==29
    fetch.assert_not_called();process.assert_not_called()


def test_native_observers_have_fixed_interval_paths_and_owned_cleanup(tmp_path):
    repo=tmp_path/'repo with spaces';repo.mkdir()
    with patch.object(install,'command',return_value=Mock(returncode=0)),patch.object(install,'reload_agent',return_value=True):
        install.install('mini',repo,tmp_path)
        install.install('nucbox',repo,tmp_path)
    plist=plistlib.loads((tmp_path/'Library/LaunchAgents/com.storypool.codex-quota.plist').read_bytes())
    assert plist['StartInterval']==300 and plist['RunAtLoad'] is True
    assert plist['WorkingDirectory']==str(repo)
    assert plist['EnvironmentVariables']['PYTHONPATH']==str(repo)
    unit=(tmp_path/'.config/systemd/user/storypool-codex-quota.service').read_text()
    assert 'WorkingDirectory='+str(repo)+'\n' in unit
    assert 'KillMode=control-group' in unit and 'TimeoutStartSec=30' in unit
    assert 'PYTHONPATH='+str(repo) in unit
    assert '--feature' not in unit and 'codex_quota_observer' in unit
    timer=(tmp_path/'.config/systemd/user/storypool-codex-quota.timer').read_text()
    assert 'OnUnitActiveSec=300s' in timer
    reader=tmp_path/'.local/bin/storypool-codex-quota-read'
    assert 'cluster.control_plane.codex_quota "$@"\n' in reader.read_text()
    assert stat.S_IMODE(reader.stat().st_mode)==0o700
    for name in ('mini','nucbox'):
        script=(Path(install.__file__).parent/'install'/('install-all-'+name+'.sh')).read_text()
        assert 'codex_quota_install --machine '+name in script


@pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('systemd-analyze'),
                    reason='Native systemd parser requires Linux')
def test_generated_observer_units_pass_native_systemd_parser(tmp_path):
    repo=tmp_path/'repo with spaces and % marker';repo.mkdir()
    with patch.object(install,'command',return_value=Mock(returncode=0)):
        install.install('nucbox',repo,tmp_path)
    units=tmp_path/'.config/systemd/user'
    result=subprocess.run(['systemd-analyze','--user','verify',
                           str(units/'storypool-codex-quota.service'),
                           str(units/'storypool-codex-quota.timer')],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('directory',['relative-repo','/tmp/repo\nEnvironment=BAD=1'])
def test_systemd_working_directory_rejects_nonabsolute_or_control_text(tmp_path,directory):
    with patch.object(install,'command') as command:
        with pytest.raises(ValueError,match='absolute repository'):
            install.install('nucbox',directory,tmp_path)
    command.assert_not_called()
