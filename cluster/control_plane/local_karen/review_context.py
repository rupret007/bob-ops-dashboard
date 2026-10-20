"""Review context routing and changed-line evidence validation.

The fixed reviewer wrappers supply the canonical module bindings. PR data and
provider replies cannot select that owner. Source verification, context budgets
and independent verdict requirements remain unchanged.
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


def _context_resolution(owner, result, code, counts):
    """Log request shape and controlled outcomes without paths or model text."""
    metadata = dict(changed_requests=counts[0], source_requests=counts[1], code=code)
    owner.logger.info('Review context: changed_requests=%s; source_requests=%s; code=%s',
                counts[0], counts[1], code)
    return owner.replace(result, scope=dict(result.scope or {}, context_resolution=metadata))


def _context_part(owner, part, entries, label):
    phase = 'supplemental' if label == 'Supplemental changed-file context' else 'requested'
    return owner.replace(part, review_material=owner.ReviewMaterial(
        tuple(dict(item) for item in entries), phase))


def _review_with_requested_context(owner, invoke, part, original_pr, source_context=None,
                                   initial_context=None):
    """One follow-up with changed diffs or separately proven immutable source."""
    from .chunk_context import (MAX_CONTEXT_FILES, MAX_CONTEXT_SIZE, context_size, _file_sections,
                                requested_file_diff)
    context, automatic_code = initial_context or ([], 'not_applicable')
    context = list(context)
    # Context is server-owned state, never reconstructed from model/PR prose.
    # Revalidate provenance so an accidental caller mismatch cannot supply data.
    if context and (original_pr is None or
            (part.owner, part.repo, part.number, part.head_sha) !=
            (original_pr.owner, original_pr.repo, original_pr.number, original_pr.head_sha) or
            any(not isinstance(item, dict) or set(item) != {'path', 'diff'}
                or not isinstance(item['path'], str) or not isinstance(item['diff'], str)
                or requested_file_diff(original_pr, item['path'], part.head_sha) is None
                or item['diff'] != requested_file_diff(original_pr, item['path'], part.head_sha)
                for item in context)):
        context, automatic_code = [], 'changed_unavailable'
    if len(context) > MAX_CONTEXT_FILES or max(context_size(context)) > MAX_CONTEXT_SIZE:
        context, automatic_code = [], 'context_budget'
    raw, serialized = context_size(context)
    automatic = dict(code=automatic_code, changed_files=len(context),
                     raw_size=raw, serialized_size=serialized)
    owner.logger.info('Initial review context: code=%s; changed_files=%s; raw_size=%s; serialized_size=%s',
                automatic_code, len(context), raw, serialized)
    supplied_part = owner._context_part(part, context, 'Supplemental changed-file context') if context else part
    result = owner._validate_chunk_result(invoke(supplied_part), original_pr)
    result = owner.replace(result, scope=dict(result.scope or {}, initial_context=automatic))
    counts = tuple(len(paths) if isinstance(paths, list) else None
                   for paths in (result.requested_files, result.requested_source_files))
    if not (result.requested_files or result.requested_source_files):
        result = owner._context_resolution(result, 'not_requested', counts)
        return result, [result]
    requests = (result.requested_files, result.requested_source_files)
    if (any(not isinstance(paths, list) or any(not isinstance(p, str) for p in paths) for paths in requests)
            or sum(len(paths) for paths in requests) > 4):
        error = owner.replace(result, verdict=owner.ReviewVerdict.ERROR,
                        reason='Invalid context request; retry required', raw_response='', findings=None,
                        requested_files=[], requested_source_files=[],
                        scope={'context_incomplete': True, 'initial_context': automatic})
        error = owner._context_resolution(error, 'invalid_request', counts)
        return error, [error]
    initial = result
    original_pr = original_pr or part
    # Changed and immutable source evidence are distinct entries even for one
    # path. Deduplicate overlaps without granting a second four-file/48k budget.
    keys = {('diff', item['path']) for item in context}
    # A source request for another changed file may be answered with new diff
    # evidence. Never claim that a changed-file diff is complete source, repeat
    # already supplied evidence, or bypass the immutable-source privacy guard.
    current_paths = {item['path'] for item in _file_sections(part.diff) if item['path']}
    if isinstance(part.review_scope, owner.ReviewScope):
        current_paths.update(part.review_scope.data.get('current_chunk', {}).get('files', []))
    supplied_paths = ({item['path'] for item in supplied_part.review_material.entries}
                      if supplied_part.review_material is not None else set())
    source_diffs = {}
    if ((part.owner, part.repo, part.number, part.head_sha) ==
            (original_pr.owner, original_pr.repo, original_pr.number, original_pr.head_sha)
            and current_paths and part.diff and part.diff in original_pr.diff):
        for path in result.requested_source_files:
            if path not in current_paths and path not in supplied_paths:
                diff = requested_file_diff(original_pr, path, head_sha=part.head_sha)
                if diff is not None:
                    source_diffs[path] = diff
    wanted = list(dict.fromkeys([('diff', path) for path in result.requested_files] +
                               [('diff' if path in source_diffs else 'source', path)
                                for path in result.requested_source_files]))
    if len(keys | set(wanted)) > MAX_CONTEXT_FILES:
        result = owner._context_resolution(result, 'context_budget', counts)
        return result, [result]
    for path in result.requested_files:
        if ('diff', path) in keys:
            continue
        supplied = requested_file_diff(original_pr, path, head_sha=part.head_sha)
        if supplied is None:
            result = owner._context_resolution(result, 'changed_unavailable', counts)
            return result, [result]
        context.append({"path": path, "diff": supplied})
        keys.add(('diff', path))
        if max(context_size(context)) > MAX_CONTEXT_SIZE:
            result = owner._context_resolution(result, 'context_budget', counts)
            return result, [result]
    if result.requested_source_files:
        from .source_context import PinnedSourceContext, SourceUnavailable
        resolver = source_context or PinnedSourceContext(original_pr)
        try:
            for path in result.requested_source_files:
                if path in source_diffs:
                    if ('diff', path) not in keys:
                        context.append({'path': path, 'diff': source_diffs[path]})
                        keys.add(('diff', path))
                        if max(context_size(context)) > MAX_CONTEXT_SIZE:
                            result = owner._context_resolution(result, 'context_budget', counts)
                            return result, [result]
                    continue
                if ('source', path) in keys:
                    continue
                source = resolver.fetch(part, path)
                context.append(source)
                keys.add(('source', path))
                if max(context_size(context)) > MAX_CONTEXT_SIZE:
                    raise SourceUnavailable('source_budget')
        except SourceUnavailable as exc:
            # SourceUnavailable enforces a fixed code allowlist; do not expose
            # requested paths, model reasons, source values or exception text.
            owner.logger.info('Source context unavailable: code=%s', exc.code)
            scope = dict(result.scope or {}, source_context_error=exc.code)
            scope.pop('source_metadata_reason', None)
            detail = getattr(exc, 'metadata_reason', None)
            if (exc.code == 'source_metadata' and type(detail) is str
                    and detail in SourceUnavailable.METADATA_REASONS):
                scope['source_metadata_reason'] = detail
            result = owner._context_resolution(owner.replace(result, scope=scope), 'source_unavailable', counts)
            return result, [result]
    label = ('Requested changed-file context' if result.requested_files or source_diffs
             else 'Requested immutable source context')
    # Build from the base description, replacing rather than appending the
    # initial packet. Every invocation has one cumulative bounded packet.
    followup = owner._context_part(part, context, label)
    initial = owner._context_resolution(initial, 'context_supplied', counts)
    from .gate import review_checkpoint
    review_checkpoint()
    if source_diffs:
        owner.logger.info('Source requests routed to changed diffs: count=%s', len(source_diffs))
    result = owner._validate_chunk_result(invoke(followup), original_pr)
    scope = dict(result.scope or {}, initial_context=automatic)
    if source_diffs:
        scope['source_requests_answered_with_diff'] = len(source_diffs)
    if initial.requested_source_files:
        scope['source_context'] = [{k: v for k, v in item.items() if k != 'source'}
                                   for item in context if 'source' in item]
    scope["context_attempts"] = [{"requested_files": initial.requested_files,
                                  "requested_source_files": initial.requested_source_files,
                                  "verdict": initial.verdict.value,
                                  "reason": initial.reason}]
    result = owner.replace(result, scope=scope)
    result = owner._context_resolution(result, 'followup_complete', counts)
    return result, [result]
