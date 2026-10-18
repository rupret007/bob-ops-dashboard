"""The shipped broker ACL must support durable retry fencing, not administration."""
import asyncio

import pytest

from cluster.control_plane.coordination import ClaimClient
from cluster.tests.nats_test_util import EphemeralNats


def test_installed_service_identity_can_fence_retries_but_cannot_administer():
    server = EphemeralNats(secured=True)
    server.start()
    try:
        operator = ClaimClient(server.url, user='coordop', password=server.passwords['coordop'],
                               inbox_prefix=b'_INBOX_coordop')
        operator.call('bootstrap', bootstrap=True)
        operator.call('bootstrap', bootstrap=True)
        service = ClaimClient(server.url, user='coord', password=server.passwords.get('coord', 'fixture-only'),
                              inbox_prefix=b'_INBOX_coord')
        claim = service.acquire('owner/repo', 7, 'a' * 40, 'mini')
        try:
            assert service.retry_state(claim) is None
            state = dict(attempts=1, retry_after=123.0, code='transient')
            service.retry_state(claim, state)
            assert service.retry_state(claim) == state
            state['code'] = 'missing-lockfile'
            service.retry_state(claim, state)
            assert service.retry_state(claim)['code'] == 'missing-lockfile'
            asyncio.run(assert_admin_denied(server))
            asyncio.run(assert_rollup_denied(server, claim))
        finally:
            service.call('release', claim)
    finally:
        server.stop()


async def assert_rollup_denied(server, claim):
    import nats
    from nats.js.errors import APIError
    nc = await nats.connect(server.url, user='coord', password=server.passwords['coord'],
                            inbox_prefix=b'_INBOX_coord')
    try:
        js = nc.jetstream()
        for bucket in ('STORYPOOL_CLAIMS', 'STORYPOOL_CLAIMS_RETRIES'):
            with pytest.raises(APIError):
                await js.publish('$KV.' + bucket + '.rollup_probe', b'fixture-only',
                                 headers={'Nats-Rollup': 'all'})
        kv = await js.key_value('STORYPOOL_CLAIMS')
        assert (await kv.get(claim['key'])).revision == claim['revision']
    finally:
        await nc.close()


async def assert_admin_denied(server):
    import nats
    errors = asyncio.Queue()

    async def error_cb(error):
        await errors.put(error)

    nc = await nats.connect(server.url, user='coord', password=server.passwords['coord'],
                            inbox_prefix=b'_INBOX_coord', error_cb=error_cb)
    try:
        for subject in ('$JS.API.STREAM.CREATE.UNTRUSTED', '$JS.API.STREAM.DELETE.KV_STORYPOOL_CLAIMS',
                        '$JS.API.STREAM.PURGE.KV_STORYPOOL_CLAIMS_RETRIES', '$KV.OTHER.forbidden',
                        '$JS.API.STREAM.INFO.OTHER', 'jobs.test.injected'):
            await nc.publish(subject, b'{}')
            await nc.flush()
            error = await asyncio.wait_for(errors.get(), timeout=2)
            assert 'permissions violation' in str(error).lower()
    finally:
        await nc.close()


from pathlib import Path

from cluster import coordination_buckets
from cluster.nats_acl import coord_permissions, render_coord_acl, verify_coord_acl

TEMPLATE = Path(__file__).resolve().parents[2] / 'nats.conf.template'


def test_shipped_acl_matches_runtime_bucket_registry():
    text = TEMPLATE.read_text()
    verify_coord_acl(text)
    assert render_coord_acl(text) == text
    assert ClaimClient('nats://fixture.invalid').bucket == coordination_buckets.bucket_name('claims')
    for name, _ttl in coordination_buckets.BUCKETS.values():
        assert '$KV.' + name + '.>' in coord_permissions()['publish']
    for subject in coord_permissions()['publish']:
        assert '.CREATE.' not in subject and '.UPDATE.' not in subject
        assert '.DELETE.' not in subject and '.PURGE.' not in subject


def test_future_bucket_requires_acl_regeneration_and_is_generated(monkeypatch):
    text = TEMPLATE.read_text()
    monkeypatch.setitem(coordination_buckets.BUCKETS, 'future', ('STORYPOOL_FUTURE', 60))
    with pytest.raises(ValueError, match='STORYPOOL_FUTURE'):
        verify_coord_acl(text)
    generated = render_coord_acl(text)
    verify_coord_acl(generated)
    for operator in (False, True):
        assert '$JS.API.STREAM.MSG.GET.KV_STORYPOOL_FUTURE' in coord_permissions(operator)['publish']
    assert coordination_buckets.bucket_name('future') == 'STORYPOOL_FUTURE'


def test_missing_retry_subject_blocks_preflight_without_disclosing_config():
    text = TEMPLATE.read_text().replace('"$JS.API.STREAM.MSG.GET.KV_STORYPOOL_CLAIMS_RETRIES", ', '', 1)
    with pytest.raises(ValueError, match='coord publish') as error:
        verify_coord_acl(text)
    assert '$JS.API.STREAM.MSG.GET.KV_STORYPOOL_CLAIMS_RETRIES' in str(error.value)
    assert 'TOKEN' not in str(error.value)


def test_overbroad_acl_is_rejected():
    text = TEMPLATE.read_text().replace('publish: [', 'publish: ["$JS.API.>", ', 1)
    with pytest.raises(ValueError, match='ACL drift'):
        verify_coord_acl(text)



def test_account_registry_uses_same_permissions_and_reserves_service_names():
    from cluster.registry import render_conf, RESERVED_NAMES
    assert {'coord', 'coordop'} <= RESERVED_NAMES
    # Renderer accepts hashes only; this synthetic string is never a credential.
    fixture_hash = '$2b$04$' + 'a' * 53
    reg = {'legacy': {}, 'nodes': {}, 'pending': {},
           'service': {'coord': fixture_hash, 'coordop': fixture_hash}}
    verify_coord_acl(render_conf(reg, '/tmp/fixture-store'))


def test_command_preflight_returns_failure_for_stale_acl(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'stale.conf'
    path.write_text(TEMPLATE.read_text().replace(
        '"$KV.STORYPOOL_CLAIMS_RETRIES.>", ', '', 1))
    result = subprocess.run([sys.executable, '-m', 'cluster.nats_acl', '--check', str(path)],
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert 'STORYPOOL_CLAIMS_RETRIES' in result.stderr
    assert 'TOKEN' not in result.stderr
