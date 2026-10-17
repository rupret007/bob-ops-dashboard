import asyncio
import json
import unittest

from cluster.submit import wait_result_from_sub
from cluster.worker_logic import fetch_or_empty


class FakeMsg:
    def __init__(self, data: bytes) -> None:
        self.data = data


class FakePsub:
    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)

    async def fetch(self, n: int, timeout: float | None = None):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSub:
    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.timeouts: list[float | None] = []

    async def next_msg(self, timeout: float | None = None):
        self.timeouts.append(timeout)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TestFetchOrEmpty(unittest.IsolatedAsyncioTestCase):
    async def test_idle_timeouts_then_messages(self):
        outcomes: list = [asyncio.TimeoutError()]
        nats_timeout = False
        try:
            import nats.errors

            outcomes.append(nats.errors.TimeoutError())
            nats_timeout = True
        except ImportError:
            pass
        sentinel = [object(), object()]
        outcomes.append(sentinel)

        psub = FakePsub(outcomes)
        self.assertEqual(await fetch_or_empty(psub, 2, timeout=5), [])
        if nats_timeout:
            self.assertEqual(await fetch_or_empty(psub, 2, timeout=5), [])
        msgs = await fetch_or_empty(psub, 2, timeout=5)
        self.assertIs(msgs, sentinel)

    async def test_builtin_timeout_error(self):
        psub = FakePsub([TimeoutError()])
        self.assertEqual(await fetch_or_empty(psub, 1, timeout=1), [])

    async def test_non_idle_errors_propagate(self):
        """Non-timeout errors must escape; fails if except is widened to Exception/BaseException."""
        import nats.errors
        from nats.js.errors import APIError

        cases = [
            ("NoRespondersError", nats.errors.NoRespondersError()),
            ("AuthorizationError", nats.errors.AuthorizationError()),
            ("ConnectionClosedError", nats.errors.ConnectionClosedError()),
            ("APIError", APIError(code=500, description="boom", err_code=100)),
            ("CancelledError", asyncio.CancelledError()),
        ]
        for label, exc in cases:
            with self.subTest(label=label):
                psub = FakePsub([exc])
                with self.assertRaises(type(exc)):
                    await fetch_or_empty(psub, 1, timeout=1)


class TestWaitResultFromSub(unittest.IsolatedAsyncioTestCase):
    async def test_idle_then_matching_result(self):
        job_id = "job-abc"
        payload = json.dumps({"id": job_id, "status": "ok"}).encode()
        sub = FakeSub(
            [
                asyncio.TimeoutError(),
                asyncio.TimeoutError(),
                FakeMsg(payload),
            ]
        )
        result = await wait_result_from_sub(sub, job_id, timeout=30.0)
        self.assertEqual(result, {"id": job_id, "status": "ok"})

    async def test_deadline_exceeded_returns_none(self):
        async def always_timeout(timeout: float | None = None):
            raise asyncio.TimeoutError()

        sub = FakeSub([])
        sub.next_msg = always_timeout  # type: ignore[method-assign]
        result = await wait_result_from_sub(sub, "j1", timeout=0.02)
        self.assertIsNone(result)

    async def test_next_msg_timeout_clamped_to_remaining(self):
        """next_msg timeout must be <= remaining; fails if min(5.0, remaining) becomes 5.0."""
        recorded: list[float | None] = []

        async def record_and_idle(timeout: float | None = None):
            recorded.append(timeout)
            raise asyncio.TimeoutError()

        sub = FakeSub([])
        sub.next_msg = record_and_idle  # type: ignore[method-assign]
        overall = 1.2
        result = await wait_result_from_sub(sub, "j1", timeout=overall)
        self.assertIsNone(result)
        self.assertGreaterEqual(len(recorded), 1)
        for t in recorded:
            self.assertIsNotNone(t)
            assert t is not None
            self.assertGreater(t, 0.0)
            # Overall wait is < 5s, so every remaining is < 5; a bare 5.0 would fail.
            self.assertLessEqual(t, overall)
            self.assertLess(t, 5.0)


if __name__ == "__main__":
    unittest.main()
