# Local completed-review enrollment

Backlog enrollment consults a local canonical completed verdict for the current
full head SHA, even when no result label remains on the pull request. Removing
`needs-karen` after normal completion therefore does not immediately enroll the
same locally reviewed head again. A new head, malformed artifact, incomplete
context or error still needs review. Changed CI evidence retains the existing
claimed refresh path; it does not grant approval or reset repair attempts.

This rule uses the existing local completion predicate and adds no remote
authority, synchronization or global suppression. A node without the canonical
artifact still enrolls the head. In particular, Mini cannot skip Nuc-only
completions through this local fix: trusted synchronization or an explicit
enrollment policy must address that separate gap. Label cleanup alone cannot
provide permanent duplicate prevention across those nodes.
