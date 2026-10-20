"""Ground review failures in actual changed lines at the pinned head.

The fixed reviewers facade supplies its canonical type and helper bindings.
This module preserves evidence validation and makes it independently inspectable.
"""

def _diff_line_locations(owner, pr):
    """Resolve evidence only against real hunk lines of the pinned supplied diff."""
    import shlex
    locations = set()
    path = None
    old_line = new_line = None
    for line in pr.diff.splitlines():
        if line.startswith("diff --git "):
            path = None
            old_line = new_line = None
        elif line.startswith("@@ "):
            match = owner.re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                old_line, new_line = int(match.group(1)), int(match.group(2))
        elif path and new_line is not None:
            if line.startswith("+"):
                locations.add((path, new_line))
                new_line += 1
            elif line.startswith("-"):
                locations.add((path, old_line))
                old_line += 1
            elif line.startswith(" "):
                locations.add((path, new_line))
                old_line += 1
                new_line += 1
        elif line.startswith(("+++ ", "--- ")):
            try:
                raw = line[4:]
                value = shlex.split(raw)[0] if raw.startswith('"') else raw
            except (ValueError, IndexError):
                continue
            if value.startswith("b/") or (path is None and value.startswith("a/")):
                path = value[2:]
    return locations


def _grounded_chunk_failure(owner, result, original_pr):
    """A missing file or unsupported rejection cannot become an actionable FAIL."""
    if result.requested_files or result.requested_source_files:
        return False
    findings = result.findings
    if not findings:
        return False
    defects = (findings.security_issues + findings.functional_issues +
               findings.test_coverage_issues + findings.code_quality_issues)
    locations = owner._diff_line_locations(original_pr)
    for defect in defects:
        if not isinstance(defect, str):
            continue
        # Context absence is uncertainty even if a reviewer attaches a citation.
        if owner.re.search(r"(?i)(?:insufficient|unavailable) (?:review )?context|"
                     r"missing (?:review|PR|cross-file) context|"
                     r"(?:omitted|absent|missing) from (?:this |the )?(?:chunk|diff)|"
                     r"(?:context|implementation|installer|test suite|tests|files?|diff)"
                     r"(?: (?:is|are|was|were))? "
                     r"(?:not provided|not included|not visible|not shown|not present)|"
                     r"(?:only (?:contains?|includes?)|(?:contains?|includes?) only)"
                     r".{0,50}(?:docs?|documentation|incident)|"
                     r"(?:implemented|provided|included|defined|located|available) "
                     r"(?:elsewhere|in (?:another|other) chunks?)|"
                     r"(?:cannot|can't|unable to) (?:verify|assess|review)", defect):
            continue
        for match in owner.re.finditer(r"([A-Za-z0-9_./-]+)(?::|#L)([1-9][0-9]*)", defect):
            if (match.group(1), int(match.group(2))) in locations:
                return True
    return False


def _validate_chunk_result(owner, result, original_pr):
    if original_pr is None and not (result.requested_files or result.requested_source_files):
        return result
    if result.requested_files or result.requested_source_files or (result.verdict in (owner.ReviewVerdict.FAIL, owner.ReviewVerdict.BORDERLINE)
                                   and not owner._grounded_chunk_failure(result, original_pr)):
        scope = dict(result.scope or {})
        scope["context_incomplete"] = True
        return owner.replace(result, verdict=owner.ReviewVerdict.BORDERLINE, scope=scope,
                       reason="Context or evidence incomplete; retry required: " + result.reason)
    return result
