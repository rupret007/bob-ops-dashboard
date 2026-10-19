"""Install the candidate in observation mode through the normal updater."""
from __future__ import annotations

import argparse
import os
import plistlib
from pathlib import Path

from .guardian import command, reload_agent
from .health_state import authoritative, recovery_lock


def install(machine, repo, home=None):
    home, repo = Path(home or Path.home()), Path(repo)
    source = Path(__file__).parent / 'install'
    (home / '.local/share/storypool').mkdir(parents=True, exist_ok=True)
    with recovery_lock():
        if machine == 'mini':
            template = plistlib.loads((source / 'com.storypool.health.plist').read_bytes())
            replacements = {'__HOME__': str(home), '__REPO_PATH__': str(repo), '__PATH__': os.environ.get('PATH', '')}
            def replace(value):
                if isinstance(value, dict):
                    return {k: replace(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [replace(v) for v in value]
                if isinstance(value, str):
                    for old, new in replacements.items():
                        value = value.replace(old, new)
                return value
            path = home / 'Library/LaunchAgents/com.storypool.health.plist'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(replace(template)))
            if not reload_agent(path):
                raise RuntimeError('Health agent registration failed')
        else:
            path = home / '.config/systemd/user/storypool-health.service'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((source / path.name).read_text().replace('__REPO_PATH__', str(repo).replace('%', '%%')))
            for argv in (['systemctl', '--user', 'daemon-reload'],
                         ['systemctl', '--user', 'enable', 'storypool-health.service'],
                         ['systemctl', '--user', 'restart', 'storypool-health.service']):
                if command(argv).returncode:
                    raise RuntimeError('Health service installation failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--is-authority', action='store_true')
    parser.add_argument('--machine', choices=('mini', 'nucbox'))
    args = parser.parse_args()
    if args.is_authority:
        return 0 if authoritative() else 1
    if not args.machine:
        parser.error('--machine is required for installation')
    install(args.machine, Path(__file__).resolve().parents[2])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
