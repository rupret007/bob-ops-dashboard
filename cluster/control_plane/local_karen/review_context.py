"""Bounded construction and routing of review context.

Fixed reviewer wrappers supply the canonical type, logger and validation
bindings. Findings grounding remains in the existing reviewer module; source
verification and budgets remain in source_context and chunk_context.
"""

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


def _context_keys(entries):
    return {('diff' if 'diff' in item else 'source', item['path']) for item in entries}


def _repack_partial_context(part, original_pr, entries, wanted, requested_paths):
    """Omit optional seeds only when required pinned evidence needs their space."""
    from .chunk_context import MAX_CONTEXT_FILES, MAX_CONTEXT_SIZE, context_size, _file_sections

    def fits(packet):
        return (len(_context_keys(packet) | set(wanted)) <= MAX_CONTEXT_FILES
                and max(context_size(packet)) <= MAX_CONTEXT_SIZE)

    if fits(entries) or original_pr is None or not part.diff:
        return entries
    if ((part.owner, part.repo, part.number, part.head_sha) !=
            (original_pr.owner, original_pr.repo, original_pr.number, original_pr.head_sha)):
        return entries
    start = original_pr.diff.find(part.diff)
    if start < 0 or original_pr.diff.find(part.diff, start + 1) >= 0:
        return entries
    end = start + len(part.diff)
    sections = _file_sections(original_pr.diff)
    current = {item['path'] for item in sections
               if item['start'] < end and item['end'] > start and item['path']}
    partial = {item['path'] for item in sections
               if item['start'] < end and item['end'] > start
               and not (start <= item['start'] and item['end'] <= end) and item['path']}
    # Whole integration packets keep their existing retention contract. A
    # partial-file packet must still include every required current-file diff.
    present = {item['path'] for item in entries if 'diff' in item}
    if not partial or not partial <= present:
        return entries
    required = current | set(requested_paths)
    retained = list(entries)
    for index in range(len(retained) - 1, -1, -1):
        if fits(retained):
            break
        item = retained[index]
        if 'diff' in item and item['path'] not in required:
            del retained[index]
    return retained


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
    requested_paths = set(result.requested_files + result.requested_source_files)
    omitted_optional = 0

    def repack():
        nonlocal context, keys, omitted_optional
        retained = _repack_partial_context(part, original_pr, context, wanted, requested_paths)
        omitted_optional += len(context) - len(retained)
        context = retained
        keys = _context_keys(context)

    repack()
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
        repack()
        if max(context_size(context)) > MAX_CONTEXT_SIZE:
            result = owner._context_resolution(result, 'context_budget', counts)
            return result, [result]
    reused_sources = []
    if result.requested_source_files or (source_context is not None and result.requested_files):
        from .source_context import PinnedSourceContext, SourceUnavailable
        resolver = source_context or PinnedSourceContext(original_pr)
        try:
            for path in result.requested_source_files:
                if path in source_diffs:
                    if ('diff', path) not in keys:
                        context.append({'path': path, 'diff': source_diffs[path]})
                        keys.add(('diff', path))
                        repack()
                        if max(context_size(context)) > MAX_CONTEXT_SIZE:
                            result = owner._context_resolution(result, 'context_budget', counts)
                            return result, [result]
                    continue
                if ('source', path) in keys:
                    continue
                source = resolver.fetch(part, path)
                context.append(source)
                keys.add(('source', path))
                repack()
                if max(context_size(context)) > MAX_CONTEXT_SIZE:
                    raise SourceUnavailable('source_budget')
            # A changed diff omits unchanged imports/initialization. Reuse only
            # source already proven by this review's resolver; never fetch extra
            # material, displace requested evidence or grant another budget.
            if type(resolver) is PinnedSourceContext:
                # Independent judges share verified source facts, never votes.
                # Requested material is already present and cannot be displaced.
                paths = dict.fromkeys(result.requested_files + list(resolver.cached_changed_paths(original_pr)))
                for path in paths:
                    if ('source', path) in keys:
                        continue
                    if (path in resolver.sources and
                            (part.owner, part.repo, part.number, part.head_sha) !=
                            (original_pr.owner, original_pr.repo, original_pr.number, original_pr.head_sha)):
                        raise SourceUnavailable('source_identity_or_path')
                    source = resolver.cached(part, path)
                    if source is None:
                        continue
                    packet = context + [source]
                    if len(packet) <= MAX_CONTEXT_FILES and max(context_size(packet)) <= MAX_CONTEXT_SIZE:
                        context.append(source)
                        keys.add(('source', path))
                        reused_sources.append(path)
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
            scope.pop('source_read_reason', None)
            detail = getattr(exc, 'read_reason', None)
            if (exc.code == 'source_read' and type(detail) is str
                    and detail in SourceUnavailable.READ_REASONS):
                scope['source_read_reason'] = detail
            result = owner._context_resolution(owner.replace(result, scope=scope), 'source_unavailable', counts)
            return result, [result]
    label = ('Requested changed-file context' if result.requested_files or source_diffs
             else 'Requested immutable source context')
    if reused_sources:
        label = 'Requested changed-file and cached source context'
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
    if omitted_optional:
        scope['followup_omitted_optional_count'] = omitted_optional
    if source_diffs:
        scope['source_requests_answered_with_diff'] = len(source_diffs)
    if initial.requested_source_files or reused_sources:
        scope['source_context'] = [{k: v for k, v in item.items() if k != 'source'}
                                   for item in context if 'source' in item]
    if reused_sources:
        scope['reused_source_files'] = len(reused_sources)
    scope["context_attempts"] = [{"requested_files": initial.requested_files,
                                  "requested_source_files": initial.requested_source_files,
                                  "verdict": initial.verdict.value,
                                  "reason": initial.reason}]
    result = owner.replace(result, scope=scope)
    result = owner._context_resolution(result, 'followup_complete', counts)
    return result, [result]
