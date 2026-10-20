"""Edit and test using the owning supervisor context; no independent admission."""


def edit_and_test(worker, tree, temp, repo, pr_number, head_sha, findings, codex, cursor):
    worker._stage('test-setup')
    runtime = worker.qualified_runtime(repo, head_sha, tree)
    if runtime is not None:
        if (worker.run(['git', 'rev-parse', 'HEAD'], tree) != head_sha
                or worker.run(['git', 'rev-parse', 'HEAD^{tree}'], tree) != runtime.head['tree_sha']):
            raise worker.RuntimeUnqualified()
    tests = worker.test_command(tree, runtime.python if runtime is not None else None)
    if runtime is not None and tests != [runtime.python, '-m', 'pytest', '-q']:
        raise worker.RuntimeUnqualified()
    original_files = []
    original_outside = set()
    if runtime is not None:
        original_files = runtime.validate_test_files(worker.run(
            ['git', 'ls-files', '-z', '--', 'tests/test_*.py'], tree))
        tests = runtime.test_command(original_files)
        original_outside = worker.outside_standard_tests(worker.run(
            ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '*.py'], tree))
    if tests[0] == 'npm':
        if not (tree / 'package-lock.json').is_file():
            raise worker.PreflightFailure('missing-lockfile')
        worker.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], tree)
        if (tree / 'prisma' / 'schema.prisma').is_file():
            worker.run(['npm', 'exec', '--no', '--', 'prisma', 'generate'], tree)
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
    test_env = {**worker.os.environ,
        'STORYPOOL_JOBS_DIR': str(worker.Path(temp) / 'test-jobs'),
        'STORYPOOL_METRICS_DIR': str(worker.Path(temp) / 'test-metrics'),
        'STORYPOOL_DATA_DIR': str(worker.Path(temp) / 'test-data'),
        'STORYPOOL_STATE_DIR': str(worker.Path(temp) / 'test-state'),
        'STORYPOOL_FIX_STATE_DIR': str(worker.Path(temp) / 'test-fixes')}
    if runtime is not None:
        state = worker.Path(temp) / 'test-runtime'
        runtime.prepare_state(state)
        test_env = runtime.environment(test_env, state)
    attempt_prompt = prompt
    for attempt in range(1, worker.MAX_FIX_ATTEMPTS + 1):
        worker._stage('provider')
        if cursor:
            with worker.cursor_worker.policy(tree) as model_env:
                worker.cursor_worker.validate_reply(worker.run(worker.cursor_worker.command(), tree,
                    input=attempt_prompt, env=model_env))
        else:
            worker.run([str(codex)] + worker.codex_args(), tree, input=attempt_prompt,
                **({'env': test_env} if runtime is not None else {}))
        if runtime is not None:
            worker._stage('test-setup')
            current = worker.qualified_runtime(repo, head_sha, tree)
            if current.receipt_sha256 != runtime.receipt_sha256:
                raise worker.RuntimeUnqualified()
            runtime.validate_state(state)
            files = worker.test_files(worker.run(['git', 'ls-files', '-z', '--cached', '--others',
                                   '--exclude-standard', '--', 'tests/test_*.py'], tree))
            if not set(original_files).issubset(files):
                raise worker.RuntimeUnqualified()
            outside = worker.outside_standard_tests(worker.run(['git', 'ls-files', '-z', '--cached', '--others',
                                                  '--exclude-standard', '--', '*.py'], tree))
            if not outside.issubset(original_outside):
                raise worker.RuntimeUnqualified()
            tests = runtime.test_command(sorted(files, key=lambda name: name.encode()))
        worker._stage('tests')
        try:
            worker.run(tests, tree, env=test_env)
            break
        except worker.subprocess.CalledProcessError as exc:
            if attempt >= worker.MAX_FIX_ATTEMPTS:
                raise
            output = ((exc.stdout or '') + '\n' + (exc.stderr or ''))[-worker.TEST_OUTPUT_TAIL:]
            worker.event('fix-worker', 'tests_retry', attempt=attempt,
                  max_attempts=worker.MAX_FIX_ATTEMPTS, failed_tests=worker.failing_tests(exc.stdout or ''),
                  failure_code='tests-failed', failure_stage='tests')
            attempt_prompt = (prompt + f'\n\nAttempt {attempt} still fails the test command. '
                              'Fix the code (never weaken or delete tests). Test output tail:\n'
                              + output)
