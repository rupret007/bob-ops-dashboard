"""Watchdog implementation; the public facade supplies current runtime bindings."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .watchdog import HealthCheck


def check_gh_in_path(context) -> HealthCheck:
    """Check if gh CLI is available."""
    gh_path = context.shutil.which("gh")
    if gh_path:
        return context.HealthCheck(
            name="gh_available",
            passed=True,
            message=f"gh found at {gh_path}",
        )

    common_paths = [
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        "/usr/bin/gh",
    ]
    for path in common_paths:
        if context.os.path.isfile(path) and context.os.access(path, context.os.X_OK):
            return context.HealthCheck(
                name="gh_available",
                passed=False,
                message=f"gh found at {path} but not in PATH",
                can_repair=False,
                repair_action="fix_path",
            )

    return context.HealthCheck(
        name="gh_available",
        passed=False,
        message="gh CLI not found",
        can_repair=False,
    )


def check_codex_in_path(context) -> HealthCheck:
    """Check if codex CLI is available."""
    codex_path = context.shutil.which("codex")
    if codex_path:
        return context.HealthCheck(
            name="codex_available",
            passed=True,
            message=f"codex found at {codex_path}",
        )

    home = context.Path.home()
    common_paths = [
        home / "bin" / "codex",
        home / ".local" / "bin" / "codex",
        context.Path("/usr/local/bin/codex"),
    ]
    for path in common_paths:
        if path.is_file() and context.os.access(path, context.os.X_OK):
            return context.HealthCheck(
                name="codex_available",
                passed=True,
                message=f"codex found at {path} (may not be in PATH)",
            )

    machine = context._get_machine()
    if machine == "mini":
        return context.HealthCheck(
            name="codex_available",
            passed=True,
            message="codex not local (Mini uses NucBox via SSH)",
        )

    return context.HealthCheck(
        name="codex_available",
        passed=False,
        message="codex CLI not found on NucBox",
        can_repair=False,
    )


def check_labels_exist(context, repo: str) -> HealthCheck:
    """Check if required labels exist in a repo."""
    try:
        from .github_reads import labels as read_labels
        labels = read_labels(repo)
        label_names = {lbl.get("name", "") for lbl in labels}

        missing = [lbl for lbl in context.REQUIRED_LABELS if lbl not in label_names]

        if missing:
            return context.HealthCheck(
                name=f"labels_{repo.replace('/', '_')}",
                passed=False,
                message=f"Missing labels: {', '.join(missing)}",
                can_repair=True,
                repair_action="create_labels",
            )

        return context.HealthCheck(
            name=f"labels_{repo.replace('/', '_')}",
            passed=True,
            message="All required labels exist",
        )
    except context.GitHubDeferred:
        context.logger.warning("GitHub label check deferred by API budget/backoff")
        return context.HealthCheck(name=f"labels_{repo.replace('/', '_')}", passed=True,
                           can_repair=False, message="WARNING: label check deferred by GitHub API budget; labels unverified")
    except (OSError, context.subprocess.SubprocessError, context.json.JSONDecodeError, context.GitHubReadError):
        return context.HealthCheck(
            name=f"labels_{repo.replace('/', '_')}",
            passed=False, can_repair=False, execution_error=True,
            message="Could not execute label check",
        )


def check_head_lag(context) -> HealthCheck:
    'Check if HEAD lags behind origin by more than 30 minutes.\n    \n    This detects when self-update is failing silently.\n    '
    try:
        from .self_update import check_head_lag as _check_lag, UPDATE_LOG_FILE
    except ImportError:
        return context.HealthCheck(
            name="head_lag",
            passed=True,
            message="self_update module not available",
        )

    lag_info = _check_lag()

    if lag_info.get("error"):
        return context.HealthCheck(
            name="head_lag",
            passed=False, can_repair=False, execution_error=True,
            message="Could not execute install head check",
        )

    if not lag_info.get("lagging"):
        return context.HealthCheck(
            name="head_lag",
            passed=True,
            message=f"HEAD {lag_info['local_head']} is current",
        )

    commits_behind = lag_info.get("commits_behind", 0)

    last_update_time = None
    if UPDATE_LOG_FILE.is_file():
        try:
            lines = UPDATE_LOG_FILE.read_text().strip().split("\n")
            if lines:
                last_entry = context.json.loads(lines[-1])
                last_update_time = context.datetime.fromisoformat(
                    last_entry.get("timestamp", "").replace("Z", "+00:00")
                )
        except (context.json.JSONDecodeError, ValueError, OSError):
            pass

    lag_minutes = 0
    if last_update_time:
        lag_minutes = (context.datetime.now(context.timezone.utc) - last_update_time).total_seconds() / 60

    if commits_behind > 0 and lag_minutes > context.HEAD_LAG_ALERT_MINUTES:
        return context.HealthCheck(
            name="head_lag",
            passed=False,
            message=f"HEAD {lag_info['local_head']} is {commits_behind} commits behind origin/{lag_info['remote_head']} for {int(lag_minutes)}m",
            can_repair=False,
        )

    return context.HealthCheck(
        name="head_lag",
        passed=True,
        message=f"HEAD {lag_info['local_head']} ({commits_behind} commits behind, last check {int(lag_minutes)}m ago)",
    )


def check_install_stale(context) -> HealthCheck:
    """Check if install is behind origin."""
    install_dir = context.Path(context.os.environ.get("STORYPOOL_INSTALL_DIR", "~/storypool-install")).expanduser()

    if not install_dir.is_dir():
        return context.HealthCheck(
            name="install_stale",
            passed=True,
            message="Install dir not found",
        )

    try:
        result = context.subprocess.run(
            ["git", "fetch", "origin", "--dry-run"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            return context.HealthCheck('install_stale', False, 'Install fetch check failed',
                               can_repair=False, execution_error=True)

        result = context.subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/cursor/storypool-all-in-one-47c7"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            behind = int(result.stdout.strip() or "0")
            if behind > 0:
                return context.HealthCheck(
                    name="install_stale",
                    passed=False,
                    message=f"Install is {behind} commits behind origin",
                    can_repair=True,
                    repair_action="self_update",
                )

        if result.returncode:
            return context.HealthCheck('install_stale', False, 'Install revision check failed',
                               can_repair=False, execution_error=True)
        return context.HealthCheck(
            name="install_stale",
            passed=True,
            message="Install is up to date",
        )
    except (OSError, context.subprocess.SubprocessError, ValueError):
        return context.HealthCheck(
            name="install_stale",
            passed=False, can_repair=False, execution_error=True,
            message="Could not execute install revision check",
        )
