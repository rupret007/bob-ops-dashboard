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

    async def next_msg(self, timeout: float | None = None):
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


if __name__ == "__main__":
    unittest.main()
