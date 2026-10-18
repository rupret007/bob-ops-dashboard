"""Live kernel qualification, explicitly run after provisioning cgroup delegation.

python3 -m cluster.control_plane.qualification.owner_descendant_containment
STORYPOOL_OWNER_CGROUP_ROOT must identify a private delegated cgroup-v2 root.
Missing delegation FAILS. This program spawns real normal/setsid descendants.
"""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock
from .. import fix_worker


def qualify(detached):
    with tempfile.TemporaryDirectory(prefix='webbob-containment-') as folder:
        ready, mutation = Path(folder) / 'ready', Path(folder) / 'late-write'
        child = ('import time; from pathlib import Path; '
                 f'Path({str(ready)!r}).write_text("ready"); time.sleep(.8); '
                 f'Path({str(mutation)!r}).write_text("unsafe")')
        leader = ('import subprocess,sys,time; from pathlib import Path; '
                  f'subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session={detached!r},'
                  'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
                  f'p=Path({str(ready)!r}); end=time.monotonic()+2; '
                  'exec("while not p.exists() and time.monotonic()<end: time.sleep(.01)")')
        token = fix_worker._execution_guard.set(lambda: None)
        try:
            result = fix_worker._run_claimed([sys.executable, '-c', leader], folder, Mock(lost=False))
        finally:
            fix_worker._execution_guard.reset(token)
        assert result.returncode == 0
        assert ready.exists(), 'Real child must have executed'
        time.sleep(1)
        assert not mutation.exists(), 'Descendant escaped command lifecycle'


if __name__ == '__main__':
    qualify(False)
    qualify(True)
    print('PASS: ordinary and detached descendants stopped before followups')
