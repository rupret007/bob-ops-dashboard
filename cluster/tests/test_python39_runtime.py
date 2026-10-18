"""Runtime regressions; CI installs the cluster optional dependencies."""


def test_cluster_usage_dependency_imports():
    # Runtime type aliases are evaluated even with postponed annotations.
    from cluster import live_metrics
    assert callable(live_metrics.aggregate)


def test_worker_and_publisher_construct_without_event_loop():
    import asyncio
    from unittest.mock import AsyncMock
    from cluster.mesh_worker import Executor, MeshWorker, NodeConfig
    from cluster.live_metrics import Publisher
    asyncio.set_event_loop(None)
    cfg = NodeConfig({'BOB_NODE_USER': 'mini', 'BOB_NODE_TOKEN': 'test-token',
                      'BOB_ROLE': 'member', 'BOB_MAX_LANES': '1'})
    worker = MeshWorker(cfg, Executor('member', True), providers={}, detected=set())
    publisher = Publisher(AsyncMock(), 'mini', machine=lambda: {})

    async def exercise():
        async with worker.sem:
            assert worker.sem.locked()
        assert not worker.sem.locked()
        assert await publisher.emit()
    asyncio.run(exercise())
