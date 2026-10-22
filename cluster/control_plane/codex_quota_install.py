"""Install only the periodic readonly quota observer through normal self-update."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shlex

from .codex_quota_observer import _private_directory
from .guardian import command, reload_agent


def install(machine, repo, home=None):
    repo, home = Path(repo), Path(home or Path.home())
    folder = home / '.local/share/storypool/quota'
    _private_directory(folder)
    reader = home / '.local/bin/storypool-codex-quota-read'
    reader.parent.mkdir(parents=True, exist_ok=True)
    reader.write_text('#!/bin/sh\nPYTHONPATH=' + shlex.quote(str(repo))
                      + ' exec /usr/bin/python3 -m cluster.control_plane.codex_quota "$@"\n')
    reader.chmod(0o700)
    module = 'cluster.control_plane.codex_quota_observer'
    if machine == 'mini':
        path = home / 'Library/LaunchAgents/com.storypool.codex-quota.plist'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(dict(
            Label='com.storypool.codex-quota',
            ProgramArguments=['/usr/bin/python3', '-m', module],
            WorkingDirectory=str(repo),
            EnvironmentVariables=dict(HOME=str(home), PYTHONPATH=str(repo),
                                      PATH=os.environ.get('PATH', '/opt/homebrew/bin:/usr/bin:/bin')),
            RunAtLoad=True, StartInterval=300, ExitTimeOut=5,
            StandardOutPath=str(folder / 'observer.log'), StandardErrorPath=str(folder / 'observer.log'))))
        path.chmod(0o600)
        if not reload_agent(path):
            raise RuntimeError('Quota observer registration failed')
    elif machine == 'nucbox':
        # WorkingDirectory is a single path setting, unlike Environment's
        # quoted word list. Quotes become literal filename characters here.
        directory = str(repo)
        if not repo.is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in directory):
            raise ValueError('Expected an absolute repository directory')
        directory = directory.replace('%', '%%')
        folder = home / '.config/systemd/user'
        folder.mkdir(parents=True, exist_ok=True)
        def quote(value):
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        path = home / 'bin'
        search = ':'.join((str(path), str(home / '.local/bin'), '/usr/local/bin', '/usr/bin', '/bin'))
        (folder / 'storypool-codex-quota.service').write_text(
            '[Unit]\nDescription=StoryPool readonly Codex plan quota observer\n\n'
            '[Service]\nType=oneshot\nWorkingDirectory=' + directory + '\n'
            'Environment=' + quote('HOME=' + str(home)) + '\n'
            'Environment=' + quote('PYTHONPATH=' + str(repo)) + '\n'
            'Environment=' + quote('PATH=' + search) + '\n'
            'ExecStart=/usr/bin/python3 -m ' + module + '\n'
            'TimeoutStartSec=30\nTimeoutStopSec=5\nKillMode=control-group\nUMask=0077\n'
            'Nice=10\nStandardError=null\n')
        (folder / 'storypool-codex-quota.timer').write_text(
            '[Unit]\nDescription=Observe actual Codex plan quota every five minutes\n\n'
            '[Timer]\nOnBootSec=30s\nOnUnitActiveSec=300s\n'
            'Unit=storypool-codex-quota.service\n\n[Install]\nWantedBy=timers.target\n')
        for argv in (['systemctl', '--user', 'daemon-reload'],
                     ['systemctl', '--user', 'enable', '--now', 'storypool-codex-quota.timer']):
            if command(argv).returncode:
                raise RuntimeError('Quota observer installation failed')
    else:
        raise ValueError('Unsupported machine')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine', choices=('mini', 'nucbox'), required=True)
    args = parser.parse_args()
    install(args.machine, Path(__file__).resolve().parents[2])


if __name__ == '__main__':
    main()
