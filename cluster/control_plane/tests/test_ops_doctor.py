import json
from cluster.control_plane.ops_doctor import reserve_attempt


def test_daily_limit_reserved_before_repair(tmp_path):
    assert [reserve_attempt(tmp_path, '2026-10-18') for _ in range(5)] == [True, True, True, False, False]
    assert json.loads((tmp_path / 'daily.json').read_text())['attempts'] == 3
    assert reserve_attempt(tmp_path, '2026-10-19')
