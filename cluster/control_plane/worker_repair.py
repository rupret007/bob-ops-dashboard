"""Supervised repair loop using the executing facade namespace."""
from __future__ import annotations

def _fix_pr(_worker_bindings, repo, pr_number, head_sha, findings):
    _worker_bindings['_stage']('provider')
    cursor = _worker_bindings['_provider'].get() == 'cursor'
    codex = None if cursor else _worker_bindings['_find_codex']()
    if cursor and not _worker_bindings['cursor_worker'].available():
        raise RuntimeError('Cursor worker unavailable')
    if not cursor and not codex:
        raise RuntimeError('codex CLI not found (expected ~/bin/codex)')
    _worker_bindings['_stage']('git')
    pr = _worker_bindings['json'].loads(_worker_bindings['run'](['gh', 'pr', 'view', str(pr_number), '--repo', repo,
                         '--json', 'headRefName,headRefOid,isCrossRepository,state']))
    branch = pr['headRefName']
    if pr['state'] != 'OPEN' or pr['isCrossRepository'] or branch in ('main', 'master'):
        raise _worker_bindings['PreflightFailure']('protected-pr')
    if pr['headRefOid'] != head_sha:
        raise _worker_bindings['PreflightFailure']('head-changed')
    with _worker_bindings['_private_workspace']() as temp:
        checkout = _worker_bindings['Path'](temp) / 'repo'
        tree = _worker_bindings['Path'](temp) / 'worktree'
        _worker_bindings['run'](['git', 'clone', '--no-checkout', '--single-branch', '--branch', branch,
             f'https://github.com/{repo}.git', str(checkout)])
        _worker_bindings['run'](['git', 'worktree', 'add', '--detach', str(tree), head_sha], checkout)
        _worker_bindings['_stage']('test-setup')
        runtime = _worker_bindings['qualified_runtime'](repo, head_sha, tree)
        if runtime is not None:
            if (_worker_bindings['run'](['git', 'rev-parse', 'HEAD'], tree) != head_sha
                    or _worker_bindings['run'](['git', 'rev-parse', 'HEAD^{tree}'], tree) != runtime.head['tree_sha']):
                raise _worker_bindings['RuntimeUnqualified']()
        tests = _worker_bindings['test_command'](tree, runtime.python if runtime is not None else None)
        if runtime is not None and tests != [runtime.python, '-m', 'pytest', '-q']:
            raise _worker_bindings['RuntimeUnqualified']()
        original_files = []
        original_outside = set()
        if runtime is not None:
            original_files = runtime.validate_test_files(_worker_bindings['run'](
                ['git', 'ls-files', '-z', '--', 'tests/test_*.py'], tree))
            tests = runtime.test_command(original_files)
            original_outside = _worker_bindings['outside_standard_tests'](_worker_bindings['run'](
                ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '*.py'], tree))
        if tests[0] == 'npm':
            if not (tree / 'package-lock.json').is_file():
                raise _worker_bindings['PreflightFailure']('missing-lockfile')
            _worker_bindings['run'](['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], tree)
            if (tree / 'prisma' / 'schema.prisma').is_file():
                _worker_bindings['run'](['npm', 'exec', '--no', '--', 'prisma', 'generate'], tree)
        prompt = (f'Apply these Karen findings for {repo} PR #{pr_number}:\n' +
                  '\n'.join(findings) + '\nMake focused code fixes and run the tests. '
                  'Do not weaken tests. Do not commit or push; the supervisor does that. '
                  'No sudo, force-push, main pushes, tags, releases, merges, secrets, or rad-dad-show-night.\n' +
                  ('Test command: ' + ' '.join(tests) if runtime is None else
                   'During implementation, run focused regression tests for the affected behavior '
                   f'with {runtime.python} and iterate on any failures. '
                   'The supervisor runs every Git-tracked tests/test_*.py module in Git order '
                   'in fresh isolated processes before committing or pushing; '
                   'leave that full-suite run to the supervisor.'))
        if cursor:
            prompt += '\nUse file tools only; shell, MCP and web fetch are denied. The supervisor runs tests and returns failures for iteration.'
        test_env = {**_worker_bindings['os'].environ,
            'STORYPOOL_JOBS_DIR': str(_worker_bindings['Path'](temp) / 'test-jobs'),
            'STORYPOOL_METRICS_DIR': str(_worker_bindings['Path'](temp) / 'test-metrics'),
            'STORYPOOL_DATA_DIR': str(_worker_bindings['Path'](temp) / 'test-data'),
            'STORYPOOL_STATE_DIR': str(_worker_bindings['Path'](temp) / 'test-state'),
            'STORYPOOL_FIX_STATE_DIR': str(_worker_bindings['Path'](temp) / 'test-fixes')}
        if runtime is not None:
            state = _worker_bindings['Path'](temp) / 'test-runtime'
            runtime.prepare_state(state)
            test_env = runtime.environment(test_env, state)
        attempt_prompt = prompt
        for attempt in range(1, _worker_bindings['MAX_FIX_ATTEMPTS'] + 1):
            _worker_bindings['_stage']('provider')
            if cursor:
                with _worker_bindings['cursor_worker'].policy(tree) as model_env:
                    _worker_bindings['cursor_worker'].validate_reply(_worker_bindings['run'](_worker_bindings['cursor_worker'].command(), tree,
                        input=attempt_prompt, env=model_env))
            else:
                _worker_bindings['run']([str(codex)] + _worker_bindings['codex_args'](), tree, input=attempt_prompt,
                    **({'env': test_env} if runtime is not None else {}))
            if runtime is not None:
                _worker_bindings['_stage']('test-setup')
                current = _worker_bindings['qualified_runtime'](repo, head_sha, tree)
                if current.receipt_sha256 != runtime.receipt_sha256:
                    raise _worker_bindings['RuntimeUnqualified']()
                runtime.validate_state(state)
                files = _worker_bindings['test_files'](_worker_bindings['run'](['git', 'ls-files', '-z', '--cached', '--others',
                                       '--exclude-standard', '--', 'tests/test_*.py'], tree))
                if not set(original_files).issubset(files):
                    raise _worker_bindings['RuntimeUnqualified']()
                outside = _worker_bindings['outside_standard_tests'](_worker_bindings['run'](['git', 'ls-files', '-z', '--cached', '--others',
                                                      '--exclude-standard', '--', '*.py'], tree))
                if not outside.issubset(original_outside):
                    raise _worker_bindings['RuntimeUnqualified']()
                tests = runtime.test_command(sorted(files, key=lambda name: name.encode()))
            _worker_bindings['_stage']('tests')
            try:
                _worker_bindings['run'](tests, tree, env=test_env)
                break
            except _worker_bindings['subprocess'].CalledProcessError as exc:
                if attempt >= _worker_bindings['MAX_FIX_ATTEMPTS']:
                    raise
                output = ((exc.stdout or '') + '\n' + (exc.stderr or ''))[-_worker_bindings['TEST_OUTPUT_TAIL']:]
                _worker_bindings['event']('fix-worker', 'tests_retry', attempt=attempt,
                      max_attempts=_worker_bindings['MAX_FIX_ATTEMPTS'], failed_tests=_worker_bindings['failing_tests'](exc.stdout or ''),
                      failure_code='tests-failed', failure_stage='tests')
                attempt_prompt = (prompt + f'\n\nAttempt {attempt} still fails the test command. '
                                  'Fix the code (never weaken or delete tests). Test output tail:\n'
                                  + output)
        _worker_bindings['_stage']('verification')
        if _worker_bindings['run'](['git', 'rev-parse', 'HEAD'], tree) != head_sha:
            raise RuntimeError('Worker changed HEAD unexpectedly')
        _worker_bindings['_stage']('git')
        _worker_bindings['run'](['git', 'add', '-A'], tree)
        _worker_bindings['_stage']('verification')
        if not _worker_bindings['run'](['git', 'diff', '--cached', '--name-only'], tree):
            raise _worker_bindings['WorkerFailure']('no-change', 'verification')
        _worker_bindings['ensure_no_staged_secrets'](tree)
        _worker_bindings['_stage']('git')
        _worker_bindings['run'](['git', 'commit', '-m', f'fix: address Karen findings for PR #{pr_number}'], tree)
        sha = _worker_bindings['run'](['git', 'rev-parse', 'HEAD'], tree)
        _worker_bindings['_stage']('push')
        _worker_bindings['run'](['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'], tree)
        _worker_bindings['_stage']('verification')
        remote = _worker_bindings['run'](['git', 'ls-remote', 'origin', f'refs/heads/{branch}'], tree).split()[0]
        if remote != sha:
            raise RuntimeError('Pushed SHA could not be verified')
        _worker_bindings['run'](['gh', 'pr', 'edit', str(pr_number), '--repo', repo, '--add-label', 'needs-karen'])
        return sha
