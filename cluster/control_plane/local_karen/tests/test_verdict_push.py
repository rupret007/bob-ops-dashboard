import base64
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pytest
from cluster.control_plane.local_karen import verdict_push as delivery
from cluster.control_plane.local_karen.verdict import Verdict, _verdict_filename, load_verdict
from cluster.control_plane.local_karen.config import KarenConfig


@pytest.fixture
def keys(tmp_path):
    private, public = tmp_path/'private.pem', tmp_path/'public.pem'
    subprocess.run(['openssl','genrsa','-out',str(private),'2048'],capture_output=True,check=True)
    subprocess.run(['openssl','rsa','-in',str(private),'-pubout','-out',str(public)],capture_output=True,check=True)
    return private, public


def verdict():
    return Verdict('owner','repo',1,'a'*40,False,'Review found a concrete bug',
                   datetime.fromtimestamp(1000,timezone.utc).isoformat(),'codex','low',.9,[],[
                       {'reviewer':'codex','verdict':'FAIL','reason':'file.py:12 returns incorrect result',
                        'findings':{'functional_issues':['file.py:12 returns incorrect result']}}])


def signed(keys, v=None, issued=1000):
    v=v or verdict()
    name=_verdict_filename(v.owner,v.repo,v.pr_number,v.head_sha)+'_'+v.verdict_type+'.json'
    body=json.dumps({'purpose':delivery.PURPOSE,'issued_at':issued,'verdicts':{name:v.to_json()}}).encode()
    signature=subprocess.run(['openssl','dgst','-sha256','-sign',str(keys[0])],input=body,capture_output=True,check=True).stdout
    return body,base64.b64encode(signature).decode()


def test_signed_delivery_reaches_trusted_loader_and_is_idempotent(tmp_path, keys):
    body,signature=signed(keys)
    folder=tmp_path/'verdicts'
    for _ in range(2):
        result=delivery.receive(body,signature,folder,keys[1],now=1000)
        assert result['accepted'][0]['sha']=='a'*40
    loaded=load_verdict('owner','repo',1,'a'*40,KarenConfig(verdict_dir=str(folder)))
    assert loaded and delivery.reviewed(loaded)
    from unittest.mock import patch
    from cluster.control_plane.fix_worker import findings_for_pr
    with patch('cluster.control_plane.fix_worker.KarenConfig.load',return_value=KarenConfig(verdict_dir=str(folder))):
        assert findings_for_pr('owner/repo',1,'a'*40)
        assert not findings_for_pr('owner/repo',1,'a'*12+'b'*28)


def test_unsigned_tampered_expired_rejected_without_files(tmp_path,keys):
    body,sig=signed(keys)
    for data,signature,now in [(body,'',1000),(body+b' ',sig,1000),(body,sig,1400)]:
        with pytest.raises(PermissionError):
            delivery.receive(data,signature,tmp_path/'verdicts',keys[1],now=now)
    assert not (tmp_path/'verdicts').exists()


@pytest.mark.parametrize('invalid',['unknown','short_sha','no_reviews','error','missing_second'])
def test_even_signed_incomplete_artifacts_are_rejected(tmp_path,keys,invalid):
    v=verdict()
    if invalid=='unknown': v.reason='unknown'
    if invalid=='short_sha': v.head_sha='a'*12
    if invalid=='no_reviews': v.review_results=[]
    if invalid=='error': v.status='ERROR'
    if invalid=='missing_second':
        v.passed=True
        v.review_results[0]['verdict']='PASS'
    body,sig=signed(keys,v)
    with pytest.raises(ValueError):
        delivery.receive(body,sig,tmp_path/'verdicts',keys[1],now=1000)


def test_replay_cannot_replace_newer_decision(tmp_path,keys):
    v=verdict()
    v.timestamp=datetime.fromtimestamp(1100,timezone.utc).isoformat()
    body,sig=signed(keys,v,issued=1100)
    folder=tmp_path/'verdicts'
    delivery.receive(body,sig,folder,keys[1],now=1100)
    old,sig=signed(keys)
    with pytest.raises(ValueError,match='older'):
        delivery.receive(old,sig,folder,keys[1],now=1100)


def test_authenticated_delivery_preserves_latest_for_conductor(tmp_path, keys):
    from cluster.control_plane.conductor import _read_flat_verdict, _is_approved
    folder = tmp_path/'verdicts'
    v = verdict()
    body, signature = signed(keys, v)
    delivery.receive(body, signature, folder, keys[1], now=1000)
    assert _read_flat_verdict(folder, 'owner/repo', 1, 'a'*40)['verdict'] == 'KARENFAIL'
    v.passed = True
    v.timestamp = datetime.fromtimestamp(1100, timezone.utc).isoformat()
    v.review_results[0]['verdict'] = 'PASS'
    v.review_results.append(dict(v.review_results[0], reviewer='minimax'))
    body, signature = signed(keys, v, issued=1100)
    delivery.receive(body, signature, folder, keys[1], now=1100)
    latest = folder / ('owner_repo_1_' + 'a'*12 + '_LATEST.json')
    assert json.loads(latest.read_text())['passed'] is True
    normalized = _read_flat_verdict(folder, 'owner/repo', 1, 'a'*40)
    assert normalized['verdict'] == 'KARENPASS'
    assert _is_approved({'headRefOid': 'a'*40, 'reviews': []}, normalized)
    old_body, old_sig = signed(keys)
    with pytest.raises(ValueError, match='older'):
        delivery.receive(old_body, old_sig, folder, keys[1], now=1100)
    assert latest.read_text() == v.to_json()


def security_verdict():
    from cluster.control_plane.local_karen.checks import PRInfo, check_no_secrets_in_diff
    from cluster.control_plane.local_karen.verdict import create_verdict
    diff = ('diff --git a/config.py b/config.py\n@@ -0,0 +1 @@\n+'
            + 'TOKEN=' + 'ghp_' + 'B7c9' * 9 + '\n')
    pr = PRInfo('owner', 'repo', 1, 'a' * 40, '', '', False, diff, {}, ['config.py'], [])
    check = check_no_secrets_in_diff(pr, KarenConfig())
    value = create_verdict(pr, [check], [], False,
                           check.reason + ': ' + check.details, 'deterministic_security')
    value.tier = 'high'
    value.timestamp = datetime.fromtimestamp(1000, timezone.utc).isoformat()
    return value


def test_signed_security_failure_syncs_but_cannot_authorize_merge_or_worker(tmp_path, keys):
    from unittest.mock import patch
    from cluster.control_plane.conductor import _read_flat_verdict, _is_approved
    from cluster.control_plane.fix_worker import findings_for_pr
    from cluster.control_plane.backlog import _has_verdict_for_head
    value = security_verdict()
    body, signature = signed(keys, value)
    folder = tmp_path / 'verdicts'
    delivery.receive(body, signature, folder, keys[1], now=1000)
    config = KarenConfig(verdict_dir=str(folder))
    loaded = load_verdict('owner', 'repo', 1, value.head_sha, config)
    assert loaded and loaded.to_dict() == value.to_dict()
    assert not delivery.reviewed(loaded)
    normalized = _read_flat_verdict(folder, 'owner/repo', 1, value.head_sha)
    assert normalized['verdict'] == 'KARENFAIL'
    assert not _is_approved({'headRefOid': value.head_sha, 'reviews': []}, normalized)
    with patch('cluster.control_plane.fix_worker.KarenConfig.load', return_value=config):
        assert findings_for_pr('owner/repo', 1, value.head_sha) == []
    assert _has_verdict_for_head(folder, 'owner/repo', {'number': 1, 'headRefOid': value.head_sha})
    assert not _has_verdict_for_head(folder, 'owner/repo', {'number': 1, 'headRefOid': 'b' * 40})
    assert 'B7c9' not in body.decode()


@pytest.mark.parametrize('change', ['pass', 'error', 'empty', 'wrong_check', 'wrong_rule',
                                   'raw_value', 'wrong_reason', 'wrong_reviewer'])
def test_signed_security_exception_rejects_other_unreviewed_artifacts(tmp_path, keys, change):
    value = security_verdict()
    if change == 'pass': value.passed = True
    if change == 'error': value.status = 'ERROR'
    if change == 'empty': value.check_results = []
    if change == 'wrong_check': value.check_results[0]['check'] = 'ci_green'
    if change == 'wrong_rule': value.check_results[0]['details'] = 'config.py:1: unknown (value redacted)'
    if change == 'raw_value': value.check_results[0]['details'] += '\nraw finding contents'
    if change == 'wrong_reason': value.reason = 'Potential issue without exact evidence'
    if change == 'wrong_reviewer': value.reviewer = 'codex'
    body, signature = signed(keys, value)
    with pytest.raises(ValueError):
        delivery.receive(body, signature, tmp_path / 'verdicts', keys[1], now=1000)
    assert not (tmp_path / 'verdicts').exists()


def test_push_includes_redacted_security_failure(tmp_path, keys):
    from unittest.mock import patch
    from cluster.control_plane.local_karen.verdict import save_verdict
    config = KarenConfig(verdict_dir=str(tmp_path / 'verdicts'), state_dir=str(tmp_path))
    value = security_verdict()
    save_verdict(value, config)
    captured = []
    def send(request, **kwargs):
        import io
        captured.append(request)
        receipt = delivery.receive(request.data, request.get_header('X-karen-signature'),
                                   tmp_path / 'receiver', keys[1])
        return io.BytesIO(json.dumps(receipt).encode())
    with patch.object(delivery.urllib.request, 'urlopen', side_effect=send):
        receipt = delivery.push(config, 'http://localhost/test', keys[0])
    assert receipt['accepted'] == [{'repo': 'owner/repo', 'pr': 1, 'sha': value.head_sha, 'passed': False}]
    assert len(captured) == 1
