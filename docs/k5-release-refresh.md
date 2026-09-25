# K5 — Release-triggered board refresh

**Goal:** After a WebJam (or other) `release.published`, nudge the ops board
refresh so Latest/chip flips without waiting up to 15m for cron.

**Listener (shipped #60):** `.github/workflows/refresh-dashboard.yml`

```yaml
on:
  schedule: [{ cron: "*/15 * * * *" }]   # reconciliation fallback
  workflow_dispatch:                     # manual
  repository_dispatch:
    types: [refresh, release-published]  # event nudge
```

## Paths that work today (no new secrets)

| Path | How |
|------|-----|
| Manual Actions | Actions → Refresh Bob Ops Dashboard → Run workflow |
| Box / conductor | `./scripts/dispatch-board-refresh.sh release-published` (ambient `gh` PAT) |
| Finish-out | After `gh release view` proves Latest, run the dispatch script (see finish-out card) |

Example with payload:

```bash
TAG=v0.28.4 \
RELEASE_URL=https://github.com/rupret007/webjam/releases/tag/v0.28.4 \
SOURCE_REPO=rupret007/webjam \
  ./scripts/dispatch-board-refresh.sh release-published
```

## Cross-repo Actions path — BLOCKED until PAT secret exists

`GITHUB_TOKEN` in `rupret007/webjam` **cannot** `repository_dispatch` into
`rupret007/bob-ops-dashboard` (token is repo-scoped). Neither repo currently
has Actions secrets (`gh secret list` → empty).

**Do not invent a new secret from agents.** When Jeff adds one:

1. Create repo secret on **webjam** named `BOB_OPS_DISPATCH_TOKEN`
   (classic PAT or fine-grained with `contents: write` / dispatch on bob-ops-dashboard).
2. Add workflow `.github/workflows/nudge-bob-ops-on-release.yml` on webjam:

```yaml
name: Nudge bob-ops board on release
on:
  release:
    types: [published]
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - name: repository_dispatch → bob-ops-dashboard
        env:
          GH_TOKEN: ${{ secrets.BOB_OPS_DISPATCH_TOKEN }}
        run: |
          set -euo pipefail
          if [[ -z "${GH_TOKEN}" ]]; then
            echo "BOB_OPS_DISPATCH_TOKEN missing — skip (cron remains fallback)"
            exit 0
          fi
          gh api --method POST \
            -H "Accept: application/vnd.github+json" \
            /repos/rupret007/bob-ops-dashboard/dispatches \
            -f event_type=release-published \
            --raw-field "client_payload=$(jq -nc \
              --arg tag "${{ github.event.release.tag_name }}" \
              --arg url "${{ github.event.release.html_url }}" \
              --arg src "${{ github.repository }}" \
              '{tag:$tag,release_url:$url,source_repo:$src}')"
```

Until that secret exists: use the box script or `workflow_dispatch` after finish-out.
Cron every 15m remains the reconciliation fallback.
