import plistlib
from pathlib import Path
from unittest.mock import Mock, patch

from cluster.control_plane import health_install as install


def test_native_definitions_preserve_restart_and_paths(tmp_path):
    repo = tmp_path / 'repo with spaces'
    repo.mkdir()
    with patch.object(install, 'command', return_value=Mock(returncode=0)), \
            patch.object(install, 'reload_agent', return_value=True):
        install.install('mini', repo, tmp_path)
        install.install('nucbox', repo, tmp_path)
    plist = plistlib.loads((tmp_path / 'Library/LaunchAgents/com.storypool.health.plist').read_bytes())
    assert plist['KeepAlive'] is True and plist['ThrottleInterval'] >= 10
    assert plist['WorkingDirectory'] == str(repo)
    assert plist['EnvironmentVariables']['PYTHONPATH'] == str(repo)
    unit = (tmp_path / '.config/systemd/user/storypool-health.service').read_text()
    assert 'Restart=on-failure' in unit and 'RestartSec=15' in unit
    assert 'health_service --machine nucbox' in unit
    assert 'WatchdogSec=' not in unit


def test_installer_preserves_disabled_legacy_units_after_cutover():
    root = Path(install.__file__).parent / 'install'
    for name in ('mini', 'nucbox'):
        script = (root / ('install-all-' + name + '.sh')).read_text()
        for function in ('install_watchdog', 'install_guardian', 'install_ops_doctor', 'install_shadow'):
            block = script.split(function + '() {', 1)[1].split('\n}', 1)[0]
            assert 'health_install --is-authority' in block
        assert 'health_install --machine ' + name in script
