# Dashboard visual review

Full-page Chromium captures at **390 × 844** (phone) and **1440 × 1000** (desktop), at 1× pixel density. Pages were served locally; external requests were blocked.

| View | Before | After |
| --- | --- | --- |
| Phone | [Before](before-phone.png) | [After](after-phone.png) |
| Desktop | [Before](before-desktop.png) | [After](after-desktop.png) |

The before images show the checked-in page at `616158d` (September 27, 2026), including its overdue-snapshot notice. The after images use the redesigned page and an actual read-only GitHub refresh, not synthetic or invented activity. No running work was verified in that snapshot; missing evidence timestamps are explicitly shown as **Age unknown**. The after snapshot was collected at **Sun Sep 27, 2026 · 5:59 PM CDT**.

The new view has five compact LLM rows, five visible project cards, and collapsed detail. Web Bob maps to `bob-ops-dashboard`. AdoptIQ and Storyland Fantasy retain their high-level private boundaries, so the three repository metadata fields are labeled Private.

Validation: 14 shared-renderer tests, 114 Python metadata/refresh tests, and Chromium checks at 320, 390, 768, and 1280px. Browser checks include full text/no horizontal overflow, keyboard details, preserved focus and disclosure state, timestamp-only updates, stale-cache rejection, failure/retry recovery, heartbeat expiry, tab hide/resume races, and rendering without JavaScript. Offline fixtures are explicitly labeled and never used as the published snapshot.

Reproduce the images with `node scripts/capture-review.js . after` after rebuilding the board. The screenshot script preserves the capture viewport and includes the whole page.
