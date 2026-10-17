# Incident: rad-dad-show-night unauthorized merges (2026-10-17)

## Summary

Four pull requests on `rupret007/rad-dad-show-night` were merged overnight
without a ship-gate PASS check. This repo is designated "never-touch" — Jeff
alone decides its PRs. The suspected root cause is that the `proj-max/` lane
worker had no never-touch repository check before executing merge operations.

## Timeline (UTC)

| Time | PR | Branch | Event |
|------|----|----|-------|
| 2026-10-17T03:42:54Z | [#67](https://github.com/rupret007/rad-dad-show-night/pull/67) | `proj-max/R10-validate-clone-dates-and-paired-hours` | Merged by rupret007 |
| 2026-10-17T04:39:07Z | [#64](https://github.com/rupret007/rad-dad-show-night/pull/64) | `proj-max/R16-fix` | Merged by rupret007 |
| 2026-10-17T05:00:27Z | [#59](https://github.com/rupret007/rad-dad-show-night/pull/59) | `proj-max/R13-fix` | Merged by rupret007 |
| 2026-10-17T06:06:14Z | [#62](https://github.com/rupret007/rad-dad-show-night/pull/62) | `proj-max/R14-fix` | Merged by rupret007 |

All four PRs were created from `proj-max/` prefixed branches on 2026-10-16 and
merged between 03:42 and 06:06 UTC the following night.

## Investigation findings

### Merge actor

All four PRs show `merged_by: rupret007` in GitHub's API. The merge commits
list `author.email: jeffstory007@gmail.com` and `committer.email: noreply@github.com`,
indicating these were standard GitHub web/API merges through Jeff's authenticated
account, not forged or force-pushed commits.

### CI status at merge time

Each PR had a `leftover` check that passed:
- PR #67: `leftover` job 117299709138 completed successfully at 03:42:36Z
- PR #64: `leftover` job 116830806905 completed successfully at 05:43:29Z (note: this ran on Oct 16, before the later merge)

However, there was no explicit `ship-gate PASS` recorded at the exact head SHA
at merge time. The `leftover-honesty` workflow is a general test suite, not the
ship-gate that should guard never-touch repos.

### Missing guard

The codebase has several related components:
1. `board_meta.py:REQUIRED_PUBLIC_REPOS` lists `rupret007/rad-dad-show-night` as a tracked repo
2. `bob-brain` repo contains `skills/qa-ship-gate/SKILL.md` describing the QA ship gate policy
3. `bob-brain/webbob/pr_ops.py` implements `ReviewGate` with exact-SHA Karen verification
4. `bob-brain/webbob/ownership.py` implements claim-based resource locking

However, none of these components implement a **fail-closed never-touch list check**
at the merge/push point. The `proj-max/` lane worker (which appears to be an
automation worker creating and pushing these branches) has no visibility into
the never-touch designation.

### Branch pattern

All affected branches follow the pattern `proj-max/R<NN>-<slug>`. This suggests
a batch automation worker (possibly named "proj-max") that:
1. Creates fix branches for multiple identified issues (R10, R13, R14, R16)
2. Pushes commits to these branches
3. Opens PRs from the rupret007 account
4. Later triggers merges without verifying ship-gate status

## Root cause

The `proj-max` lane worker had **no never-touch repository check** in its
merge code path. The system lacked:

1. A central, authoritative list of never-touch repositories
2. A fail-closed guard that blocks merge/push/PR-create operations on those repos
3. Verification that a gate PASS exists at the exact head SHA before merge

The lane worker was able to:
- Push to branches on never-touch repos
- Create PRs on never-touch repos
- Merge PRs on never-touch repos

...all without any check against Jeff's explicit designation that these repos
require human approval.

## Why the gate was bypassed

1. **No never-touch list enforcement**: While `rad-dad-show-night` is in
   `REQUIRED_PUBLIC_REPOS`, this list is for dashboard tracking, not access control.

2. **Ship-gate not wired to merge path**: The `ReviewGate` in `pr_ops.py` implements
   exact-SHA verification but:
   - It is advisory (`webbob_draft_only: True`)
   - It has no method to actually block merges
   - The lane worker didn't call it before merging

3. **leftover != ship-gate**: The `leftover-honesty` CI workflow ran and passed,
   but this is a test suite, not the ship-gate approval mechanism.

4. **Account access implicit trust**: Because the operations used Jeff's
   `rupret007` account token/credentials, GitHub allowed the merges. The
   automation should have checked policy before using those credentials.

## Remediation

### Immediate (this incident)

No reverts requested. Jeff retains final merge authority and can review the
merged changes post-facto.

### Permanent fix required

1. **Add `NEVER_TOUCH_REPOS` constant**: A central, authoritative list of repos
   that require explicit human approval for any write operation.

2. **Add fail-closed guard**: Check `NEVER_TOUCH_REPOS` before:
   - `git push` to any branch
   - `gh pr create` or API PR creation
   - `gh pr merge` or API merge calls
   - Any MCP tool call that writes to these repos

3. **Ship-gate PASS at exact SHA**: Before merge, verify that a Karen ship-gate
   PASS exists for the exact head SHA being merged. Stale PASSes must not authorize.

4. **Audit lane workers**: Review all `proj-max/`, `codex/`, `cursor/`, and other
   automated branch patterns to ensure they honor the never-touch list.

## Affected repos

- `rupret007/rad-dad-show-night` (this incident)

Candidates for never-touch list based on business context:
- `rupret007/rad-dad-show-night` — Jeff's show night run sheet
- Any other repos Jeff designates as human-only

## References

- PR #67: https://github.com/rupret007/rad-dad-show-night/pull/67
- PR #64: https://github.com/rupret007/rad-dad-show-night/pull/64
- PR #62: https://github.com/rupret007/rad-dad-show-night/pull/62
- PR #59: https://github.com/rupret007/rad-dad-show-night/pull/59
- Ship gate skill: `bob-brain/skills/qa-ship-gate/SKILL.md`
- PR ops module: `bob-brain/webbob/pr_ops.py`
