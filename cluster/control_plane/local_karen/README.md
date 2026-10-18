# Local Karen Ship Gate

A ship gate that runs on Jeff's Mac mini (or NucBox WSL) instead of a Grok-hosted bot.

## Overview

Local Karen provides deterministic checks plus LLM review for PRs before merge:

1. **Deterministic checks** (always run first):
   - Blocked repo list (e.g., `rupret007/rad-dad-show-night`)
   - PR not draft
   - CI status green at pinned SHA
   - Tests not weakened/deleted in diff
   - No secrets in diff
   - No dangerous commands (sudo, force-push, tags, releases)

2. **LLM review** (configurable order):
   - **Primary**: Codex CLI (`codex exec` via `CODEX_HOST=nuc`)
   - **Fallback**: Gemini API (prepaid)
   - **Second judge**: MiniMax (borderline/high-risk tiers)
   - **Last resort**: Claude (off by default, capped)

## Invariants

- **NEVER** merge, push, force-push, tag, release, or use sudo
- Verdict for a stale SHA is invalid
- Fail closed on any error
- Break functional and security claims before approval

## Installation

```bash
# Clone the repo and cd to it
cd cluster/control_plane/local_karen

# Run the install script (no sudo required)
./install.sh

# Add ~/.local/bin to PATH if not already
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

## CLI Usage

```bash
# Run gate on a PR
karen-gate rupret007/webjam 123

# Post verdict as PR comment
karen-gate rupret007/webjam 123 --comment

# Set budget cap
karen-gate rupret007/webjam 123 --budget 1.00

# JSON output
karen-gate rupret007/webjam 123 --json

# Dry run
karen-gate rupret007/webjam 123 --dry-run

# Verbose output
karen-gate rupret007/webjam 123 -v
```

## Polling Daemon

The poller checks for PRs with the `needs-karen` label every 10 minutes:

```bash
# Run once (for launchd/cron)
karen-poller --once

# Specify repos to poll
karen-poller --once --repos rupret007/webjam,rupret007/bob-ops-dashboard

# Run continuous loop (dev/testing only)
karen-poller
```

### launchd Setup (macOS)

The install script creates a launchd plist at `~/Library/LaunchAgents/com.storypool.karen-poller.plist`.

```bash
# Load the agent
launchctl load ~/Library/LaunchAgents/com.storypool.karen-poller.plist

# Unload the agent
launchctl unload ~/Library/LaunchAgents/com.storypool.karen-poller.plist

# Trigger manually
launchctl start com.storypool.karen-poller

# Check logs
tail -f ~/.local/share/karen/poller.log
```

## Configuration

Config file: `~/.config/karen/config.json`

```json
{
  "reviewer_order": ["codex", "gemini", "minimax"],
  "codex_host": "nuc",
  "codex_effort_default": "low",
  "codex_effort_high_risk": "high",
  "codex_weekly_cap_pct": 75.0,
  "gemini_api_key_file": "~/.config/storypool/gemini.key",
  "minimax_api_key_file": "~/.config/storypool/minimax.key",
  "claude_enabled": false,
  "claude_daily_cap": 3,
  "budget_per_run_usd": 0.50,
  "never_touch_repos": ["rupret007/rad-dad-show-night"],
  "verdict_dir": "~/.local/share/karen/verdicts",
  "state_dir": "~/.local/share/karen",
  "poll_interval_minutes": 10,
  "poll_label": "needs-karen"
}
```

### Reviewer Order

The `reviewer_order` list controls which reviewers are tried and in what order:

- `codex` - Codex CLI via `CODEX_HOST=nuc` (Jeff's $200 plan)
- `gemini` - Gemini API (prepaid)
- `minimax` - MiniMax API (BYOK, used as second judge)
- `claude` - Claude CLI (off by default, enable with `claude_enabled: true`)

### Effort Levels

Codex runs at different effort levels based on risk tier:

- `low` (default) - For low/medium risk PRs
- `high` - For high-risk and security-related PRs

### Budget Caps

- **Codex**: Stops at 75% weekly usage (configurable via `codex_weekly_cap_pct`)
- **Claude**: Max 3 runs per day (configurable via `claude_daily_cap`)
- **Per-run**: $0.50 default (configurable via `budget_per_run_usd`)

## Verdict Files

Verdicts are saved to `~/.local/share/karen/verdicts/` as JSON files:

- `{owner}_{repo}_{pr}_{sha}_KARENPASS.json`
- `{owner}_{repo}_{pr}_{sha}_KARENFAIL.json`

## Risk Tiers

PRs are classified into risk tiers that affect review intensity:

| Tier | Triggers | Codex Effort |
|------|----------|--------------|
| LOW | Simple changes, docs | low |
| MEDIUM | API changes, 1-2 risk keywords | low |
| HIGH | Auth, payment, db, 3+ risk keywords, 20+ files | high |
| SECURITY | Vulnerability, injection, oauth mentions | high |

High-risk and security tiers trigger the second judge (MiniMax) for borderline verdicts.

## API Keys

Store API keys in these files (never in environment variables for security):

- Gemini: `~/.config/storypool/gemini.key`
- MiniMax: `~/.config/storypool/minimax.key`

Codex and Claude use their existing CLI sign-ins — Local Karen never reads or copies auth files.

## Labels

The poller uses these labels:

- `needs-karen` - Triggers gate check (removed after check)
- `karen-pass` - Added on KARENPASS verdict
- `karen-fail` - Added on KARENFAIL verdict

## Uninstall

```bash
./install.sh --uninstall
```

This removes the CLI, launchd agent, and module files but keeps config and verdicts.

### Required MiniMax second review

Every PASS now requires MiniMax's independent structured PASS alongside the
primary reviewers, with findings from both. Two other reviewers cannot bypass
MiniMax, and the legacy `require_two_pass=False` argument cannot disable it.
Any FAIL/BORDERLINE, missing MiniMax key, timeout, incomplete output, malformed
response or missing findings blocks approval. Primary transport errors may still
fall back to the next configured primary. Diffs above the complete-review limit
require human review.

MiniMax reads `MINIMAX_API_KEY` first, otherwise the existing
`minimax_api_key_file` in Karen's config. No credentials are copied. Configure
`minimax_model` if needed (default `MiniMax-M2.7`). The request uses MiniMax's
[documented API](https://platform.minimax.io/docs/api-reference/text-post),
checks provider status and completion, and suppresses credential-bearing errors.
No service restart or live review is performed by this change.

### Review recovery and Mini delivery

Operational failures return `ERROR` without writing KARENFAIL or changing result
labels. The retry label remains queued. A SHA change invalidates the run before
any file is published. The poller retries old labels lacking a completed review
at the full current SHA, skips drafts, and never updates a PR branch to review it.

Codex reviews use `gpt-6-astra` at `max` (overrides: `STORYPOOL_KAREN_MODEL` and
`STORYPOOL_KAREN_EFFORT`). Complete diffs are provided, with no silent truncation.
GitHub HTTP 406 falls back to a temporary bare Git repository with pinned head and
base objects. Diffs over the review context budget remain ERROR/manual work.
PASS still requires the independent MiniMax review. Comments are opt-in with
`--comment`; artifacts and normal result labels are the default output.

The Mini pushes completed artifacts to the NucBox dashboard at
`POST /api/karen/verdicts`. RSA-SHA256 signatures cover the complete request,
purpose and issue time. The pinned verification key is `mini-verdict-public.pem`;
the private key exists only at `~/.local/share/karen/verdict-signing.pem` (0600).
Never copy it into Git or onto the NucBox. Key rotation requires replacing the
public key through the trusted deployment branch. The receiver rejects unsigned,
tampered, expired, mismatched-SHA and incomplete artifacts, and older decisions.
Public PR comments/labels are not authority. `GET /api/karen/status` reports the
last receipt and dispatch decisions; it cannot execute or enqueue work.
