"""Reuse verified bytes at one pinned PR; prior votes never provide evidence."""
from .checks import ReviewScope
from .chunk_context import (MAX_CONTEXT_FILES, MAX_CONTEXT_SIZE, context_size,
                            requested_file_diff)
from .source_context import PinnedSourceContext


def initial_cached_material(part, original, initial, resolver, automatic_code='not_applicable'):
    """Plan cached source before this reviewer receives its initial packet.

    Retain every required complete partial-file diff. Optional automatic diffs
    may give way to previously verified implementation before first delivery;
    nothing already shown to this reviewer or explicitly requested is removed.
    Reserve a follow-up slot and retain the serialized cap. No IO or prior vote
    supplies authority.
    """
    if (type(resolver) is not PinnedSourceContext or original is None
            or not isinstance(part.review_scope, ReviewScope)
            or (part.owner, part.repo, part.number, part.head_sha) !=
               (original.owner, original.repo, original.number, original.head_sha)
            or part.review_scope.data.get('head_sha') != part.head_sha
            or part.review_scope.data.get('pr_number') != part.number
            or not part.diff or part.diff not in original.diff):
        return initial
    current = part.review_scope.data.get('current_chunk', {}).get('files', [])
    if not current or not isinstance(current, list) or any(not isinstance(p, str) for p in current):
        return initial
    patches = {p: requested_file_diff(original, p, part.head_sha) for p in current}
    if any(not whole for whole in patches.values()):
        return initial
    required = {p for p, whole in patches.items() if whole not in part.diff}
    if required:
        if automatic_code != 'partial_file_supplied':
            return initial
        entries = [item for item in initial if isinstance(item, dict)
                   and set(item) == {'path', 'diff'} and item['path'] in required
                   and item['diff'] == patches[item['path']]]
        if len(entries) != len(required):
            return initial
    elif initial:
        return initial
    else:
        entries = []
    cached = sorted(resolver.previously_verified(part),
                    key=lambda item: (-item['bytes'], item['path']))
    if not cached:
        return initial
    added = 0
    for source in cached:
        if added >= 2 or len(entries) >= MAX_CONTEXT_FILES - 1:
            break
        if max(context_size(entries + [source])) <= MAX_CONTEXT_SIZE:
            entries.append(source)
            added += 1
    return entries if added else initial
