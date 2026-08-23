# Bob Ops Dashboard

Public, mobile-friendly status board for projects Bob is working on (Jeff Story / `rupret007`).

**Live URL:** https://rupret007.github.io/bob-ops-dashboard/

## What’s here

- `index.html` — human dashboard (dark, no frameworks)
- `status.json` — machine-readable snapshot for future refresh scripts
- No secrets, tokens, CSOne customer paths, Keeper material, or private handoff text
- AdoptIQ appears only as a high-level “Cisco CS desktop — Build 115” summary

## How Bob refreshes (weekday morning routine)

1. On the box, run `gh` against the tracked repos (default branch tip, open PR count, latest Actions conclusion).
2. Regenerate `index.html` + `status.json` under `/workspace/bob-project-dashboard/` with an America/Chicago `Last updated` stamp.
3. Push both files to `rupret007/bob-ops-dashboard` `main` (root = Pages source).
4. Confirm Pages URL loads; skip any private repo that becomes inaccessible and note it in the footer/JSON.

Suggested one-liner offer for Jeff:

> Want Bob to regenerate + push this ops dashboard every weekday morning (~8 AM CT) so the bookmark always shows live CI?

## Publish notes

- Repo is **public** so GitHub Pages works on the free plan.
- Pages served from `main` / root.
- Do not merge unrelated PRs as part of a refresh.

## refresh.sh

```bash
./refresh.sh          # rebuild index.html + status.json from live gh
./refresh.sh --push   # rebuild and push to Pages (main / root)
```
