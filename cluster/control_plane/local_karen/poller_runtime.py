"""Runtime and CLI orchestration; the public poller retains all injected dependencies."""
from __future__ import annotations
from typing import Optional
from .config import KarenConfig

def _run_poll_once(controller, config=None, auto_discover=True, auto_label=True, auto_update=True, post_comment=False, remove_label_on_complete=True):
    """Run a single poll cycle.

    Args:
        config: Karen configuration
        auto_discover: Whether to auto-discover repos (default True)
        auto_label: Whether to auto-label queued PRs with needs-karen (default True)
        auto_update: Whether to self-update from git (default True)

    Reads repos from environment or config, or auto-discovers if enabled.
    Auto-discovery wins over single-repo config (repos=[] or 'auto' triggers discovery).
    """
    machine = controller.os.environ.get('STORYPOOL_MACHINE', 'mini')
    install_dir_env = controller.os.environ.get('STORYPOOL_INSTALL_DIR')
    install_dir = controller.Path(install_dir_env).expanduser() if install_dir_env else None
    if auto_update:
        try:
            if controller.self_update is None:
                raise RuntimeError(controller.SELF_UPDATE_IMPORT_ERROR)
            update_result = controller.self_update(machine=machine, install_dir=install_dir)
            outcome = update_result.get('outcome', 'UNKNOWN')
            reason = update_result.get('reason', 'no reason')
            if outcome == 'UPDATED':
                controller.logger.info('Self-update UPDATED: %s -> %s (module_copied=%s, reinstalled=%s)', update_result.get('old_head', '?'), update_result.get('new_head', '?'), update_result.get('module_copied', False), update_result.get('reinstalled', False))
            elif outcome == 'SKIPPED':
                controller.logger.info('Self-update SKIPPED: %s', reason)
            elif outcome == 'FAILED':
                controller.logger.warning('Self-update FAILED: %s', reason)
            else:
                controller.logger.info('Self-update %s: %s', outcome, reason)
        except Exception as e:
            controller.logger.warning('Self-update FAILED: %s', e)
    controller.KarenConfig.migrate_config()
    if config is None:
        config = controller.KarenConfig.load()
    repos_env = controller.os.environ.get('KAREN_REPOS', '')
    if repos_env:
        repos = [r.strip() for r in repos_env.split(',') if r.strip()]
    elif auto_discover and controller.BACKLOG_AVAILABLE and config.should_auto_discover():
        controller.logger.info("Auto-discovering repos (config.repos='%s')...", config.repos)
        repos = controller.discover_repos(owner='rupret007')
        controller.logger.info('Discovered %d repos', len(repos))
    elif config.get_repos_list():
        repos = config.get_repos_list()
        controller.logger.info('Using %d repo(s) from config', len(repos))
    else:
        controller.logger.info('Auto-discovering repos (fallback)...')
        if controller.BACKLOG_AVAILABLE:
            repos = controller.discover_repos(owner='rupret007')
            controller.logger.info('Discovered %d repos', len(repos))
        else:
            repos = ['rupret007/webjam', 'rupret007/bob-ops-dashboard']
    try:
        from ..github_reads import refresh_quota
        refresh_quota()
    except (controller.GitHubDeferred, controller.GitHubReadError):
        raise
    except Exception as exc:
        controller.logger.warning('GitHub quota refresh deferred: %s', type(exc).__name__)
    controller.logger.info('Polling %d active repo(s) before optional housekeeping', len(repos))
    scan = controller.RepoScan(config, repos)
    results = controller.poll_and_run(scan.repos, config, post_comment, remove_label_on_complete, machine, scan)
    state = controller.load_poll_state(config)
    state.last_poll = controller.datetime.now(controller.timezone.utc).isoformat()
    state.prs_checked.extend(results)
    state.prs_checked = state.prs_checked[-100:]
    controller.save_poll_state(state, config)
    if scan.deferred_after_review:
        controller.logger.warning('GitHub admission deferred; completed review results preserved and housekeeping skipped')
        return results
    undrafted = []
    if auto_label and controller.BACKLOG_AVAILABLE:
        controller.logger.info('Auto-undrafting green draft PRs...')
        try:
            undrafted = controller.undraft_green_prs(repos=repos, dry_run=False, max_undrafts=controller.MAX_UNDRAFTS_PER_TICK)
            if undrafted:
                controller.logger.info('Undrafted %d PR(s) with green CI', len(undrafted))
                for pr in undrafted:
                    controller.logger.info('  Undrafted: %s#%d (%s)', pr['repo'], pr['pr'], pr['title'][:40])
        except (controller.GitHubDeferred, controller.GitHubReadError):
            controller.logger.warning('Optional GitHub housekeeping deferred; completed review results preserved')
            return results
        except Exception as e:
            controller.logger.warning('Auto-undraft failed: %s', e)
        controller.logger.info('Auto-labeling queued PRs...')
        try:
            labeled = controller.label_queued_prs(repos=repos, dry_run=False)
            if labeled:
                controller.logger.info('Labeled %d PR(s) with needs-karen', len(labeled))
        except (controller.GitHubDeferred, controller.GitHubReadError):
            controller.logger.warning('Optional GitHub housekeeping deferred; completed review results preserved')
            return results
        except Exception as e:
            controller.logger.warning('Auto-label failed: %s', e)
    controller.logger.info('Polling %d repo(s) for PRs with %s label', len(repos), config.poll_label)
    try:
        from .verdict_push import push
        receipt = push(config)
        controller.logger.info('Verdict push: %s', receipt)
    except Exception as exc:
        controller.logger.warning('Verdict push will retry: %s', type(exc).__name__)
    if controller.BACKLOG_AVAILABLE:
        controller.logger.info('Writing backlog report...')
        try:
            backlog = controller.collect_backlog(repos=repos)
            backlog['undrafted_this_tick'] = undrafted
            repo_root_env = controller.os.environ.get('STORYPOOL_INSTALL_DIR')
            repo_root = controller.Path(repo_root_env).expanduser() if repo_root_env else None
            controller.write_backlog_report(backlog, repo_root=repo_root)
            controller.logger.info('Backlog: %d PRs queued, %d draft_green, %d undrafted this tick', backlog['summary'].get('queued', 0), backlog['summary'].get('draft_green', 0), len(undrafted))
        except (controller.GitHubDeferred, controller.GitHubReadError):
            controller.logger.warning('Optional GitHub housekeeping deferred; completed review results preserved')
            return results
        except Exception as e:
            controller.logger.warning('Backlog report failed: %s', e)
        if controller.write_karen_fails_report is not None:
            try:
                fails_path = controller.write_karen_fails_report()
                controller.logger.info('Wrote Karen failures report to %s', fails_path)
            except (controller.GitHubDeferred, controller.GitHubReadError):
                controller.logger.warning('Optional GitHub housekeeping deferred; completed review results preserved')
                return results
            except Exception as e:
                controller.logger.warning('Karen failures report failed: %s', e)
    return results

def run_poll_once(controller, config=None, auto_discover=True, auto_label=True, auto_update=True, post_comment=False, remove_label_on_complete=True):
    """One review process at a time; apply Mini guards before model work."""
    import fcntl
    from ..guards import MachineGuards
    config = config or controller.KarenConfig.load()
    config.get_state_dir().mkdir(parents=True, exist_ok=True)
    with (config.get_state_dir() / 'poller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            controller.logger.info('Poll skipped: another review cycle is active')
            return []
        controller.record_runtime(config)
        guard = MachineGuards(controller.os.environ.get('STORYPOOL_MACHINE', 'mini')).check_all()
        if not guard.passed:
            controller.logger.warning('Poll deferred: %s', guard.reason)
            return []
        try:
            return controller._run_poll_once(config, auto_discover, auto_label, auto_update, post_comment, remove_label_on_complete)
        except controller.GitHubDeferred:
            controller.logger.warning('Poll cycle deferred by GitHub budget/backoff; remaining housekeeping skipped')
            return []
        except controller.RepoScanError:
            controller.logger.warning('Review scan state unavailable; cycle deferred')
            return []
        except controller.GitHubReadError:
            controller.logger.warning('GitHub discovery unavailable; cycle deferred and previous reports preserved')
            return []

def run_poll_loop(controller, config: Optional[KarenConfig]=None) -> None:
    """Run continuous polling loop.

    For development/testing only — production should use launchd.
    """
    if config is None:
        config = controller.KarenConfig.load()
    interval_sec = config.poll_interval_minutes * 60
    controller.logger.info(f'Starting poll loop with {config.poll_interval_minutes} minute interval')
    while True:
        try:
            controller.run_poll_once(config)
        except KeyboardInterrupt:
            controller.logger.info('Interrupted, stopping poll loop')
            break
        except Exception as e:
            controller.logger.error(f'Poll cycle failed: {e}')
        controller.logger.info(f'Sleeping for {config.poll_interval_minutes} minutes')
        controller.time.sleep(interval_sec)

def main(controller) -> int:
    """CLI entry point for poller."""
    import argparse
    parser = argparse.ArgumentParser(prog='karen-poller', description='Poll for PRs with needs-karen label and run gate')
    parser.add_argument('--once', action='store_true', help='Run once and exit (for launchd/cron)')
    parser.add_argument('--repos', type=str, default=None, help='Comma-separated list of repos to poll (owner/repo)')
    parser.add_argument('--comment', action='store_true', help='Opt in to posting verdict comments')
    parser.add_argument('--no-comment', action='store_true', help="Don't post verdict comments")
    parser.add_argument('--keep-label', action='store_true', help="Don't remove needs-karen label after gate")
    parser.add_argument('--config', type=controller.Path, default=None, help='Path to config file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--no-discover', action='store_true', help="Don't auto-discover repos (use KAREN_REPOS or defaults)")
    parser.add_argument('--no-label', action='store_true', help="Don't auto-label queued PRs with needs-karen")
    parser.add_argument('--no-update', action='store_true', help="Don't attempt self-update from git")
    args = parser.parse_args()
    if args.verbose:
        controller.logging.getLogger().setLevel(controller.logging.DEBUG)
    if args.config:
        config = controller.KarenConfig.load(args.config)
    else:
        config = controller.KarenConfig.load()
    if args.repos:
        controller.os.environ['KAREN_REPOS'] = args.repos
    if args.once:
        results = controller.run_poll_once(config, auto_discover=not args.no_discover, auto_label=not args.no_label, auto_update=not args.no_update, post_comment=args.comment and (not args.no_comment), remove_label_on_complete=not args.keep_label)
        for r in results:
            if 'error' in r:
                print('ERROR {repo}#{pr}: {error}'.format(**r))
            elif r.get('skipped'):
                print('SKIP {repo}#{pr}: {reason}'.format(**r))
            elif r.get('passed'):
                print('PASS {repo}#{pr}@{sha}: {reason}'.format(**{**r, 'reason': str(r.get('reason', ''))[:50]}))
            else:
                print('FAIL {repo}#{pr}@{sha}: {reason}'.format(**{**r, 'reason': str(r.get('reason', ''))[:50]}))
        return 0
    else:
        controller.run_poll_loop(config)
        return 0
