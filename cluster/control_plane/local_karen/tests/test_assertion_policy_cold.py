"""Run the exact optional-import guard without a policy-module global sentinel."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('first', ['checks', 'assertion_policy'])
@pytest.mark.parametrize('new_file', [True, False])
def test_optional_import_guard_in_cold_policy_module(first, new_file):
    root = Path(__file__).resolve().parents[4]
    line = '@unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")'
    if new_file:
        diff = ('diff --git a/tests/test_guard.py b/tests/test_guard.py\n'
            'new file mode 100644\n--- /dev/null\n+++ b/tests/test_guard.py\n'
            '@@ -0,0 +1,2 @@\n+' + line + '\n+class TestGuard: pass\n')
    else:
        diff = ('diff --git a/tests/test_guard.py b/tests/test_guard.py\n'
            '--- a/tests/test_guard.py\n+++ b/tests/test_guard.py\n'
            '@@ -1 +1,2 @@\n+' + line + '\n class TestGuard: pass\n')
    code = '''import importlib,json,sys
sys.path.insert(0,sys.argv[1])
importlib.import_module('cluster.control_plane.local_karen.'+sys.argv[2])
from cluster.control_plane.local_karen import assertion_policy,checks
from cluster.control_plane.local_karen.config import KarenConfig
assert not hasattr(assertion_policy,'_HAS_NEVER_TOUCH')
pr=checks.PRInfo(owner='example',repo='project',number=1,head_sha='a'*40,
 title='Optional import guard',author='test',is_draft=False,diff=sys.stdin.read(),
 ci_status={},changed_files=['tests/test_guard.py'],deleted_files=[])
result=assertion_policy.check_tests_not_weakened(pr,KarenConfig())
print(json.dumps(dict(verdict=result.verdict.value,details=result.details)))
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(root), first],
        input=diff, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and not result.stderr
    observation = json.loads(result.stdout)
    if new_file:
        assert observation['verdict'] == 'PASS'
    else:
        assert observation['verdict'] == 'FAIL'
        assert 'Added test skip/xfail' in observation['details']
