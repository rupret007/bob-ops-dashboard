"""The shipped broker ACL must support durable retry fencing, not administration."""
import asyncio

import pytest

from cluster.control_plane.coordination import ClaimClient
from cluster.tests.nats_test_util import EphemeralNats


def test_installed_service_identity_can_fence_retries_but_cannot_administer():
    server = EphemeralNats(secured=True)
    server.start()
    try:
        operator = ClaimClient(server.url, user='nucbox', password=server.passwords['nucbox'],
                               inbox_prefix=b'_INBOX_nucbox')
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
