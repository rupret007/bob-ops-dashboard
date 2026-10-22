"""Dashboard makes loaded-code evidence visible without granting gate authority."""
import json
from pathlib import Path

from cluster.control_plane.dashboard.tachometer import EngineCollector


def test_engine_includes_only_allowlisted_runtime_receipt(tmp_path):
    story = tmp_path / 'story'
    karen = tmp_path / 'karen'
    story.mkdir()
    karen.mkdir()
    value = {'installed_head': 'a' * 40, 'loaded_head': 'a' * 40,
             'source_matches': True, 'pid': 101, 'loaded_at': '2026-10-19T00:00:00+00:00',
             'observed_at': '2026-10-19T00:01:00+00:00', 'private': 'hidden'}
    (karen / 'poller-runtime.json').write_text(json.dumps(value))
    data = EngineCollector(story, karen, 'nucbox').collect()
    assert data['karen_runtime']['loaded_head'] == 'a' * 40
    assert 'private' not in data['karen_runtime']
    assert 'passed' not in data['karen_runtime']


def test_runtime_panel_uses_text_and_labels_unknown_evidence():
    template = Path(__file__).parents[1] / 'engine.html'
    text = template.read_text()
    assert 'id="engine-runtime"' in text
    assert "getElementById('engine-runtime').textContent" in text
    assert 'Karen loaded version is unverified' in text
