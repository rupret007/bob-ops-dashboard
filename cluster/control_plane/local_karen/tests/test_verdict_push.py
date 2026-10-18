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
