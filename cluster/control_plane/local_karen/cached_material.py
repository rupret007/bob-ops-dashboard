"""Reuse verified bytes at one pinned PR; prior votes never provide evidence."""
from .checks import ReviewScope
from .chunk_context import (MAX_CONTEXT_FILES, MAX_CONTEXT_SIZE, context_size,
                            requested_file_diff)
from .source_context import PinnedSourceContext


def initial_cached_material(part, original, initial, resolver):
    """Seed two source entries only when there is no mandatory initial packet.

    Complete primary files remain the changed boundary. Keep two entry slots
    for the normal single follow-up and retain the existing serialized cap.
    No source fetch, file IO, vote, or authority crosses the review boundary.
    """
    if (initial or type(resolver) is not PinnedSourceContext or original is None
            or not isinstance(part.review_scope, ReviewScope)
            or (part.owner, part.repo, part.number, part.head_sha) !=
               (original.owner, original.repo, original.number, original.head_sha)
            or part.review_scope.data.get('head_sha') != part.head_sha
            or part.review_scope.data.get('pr_number') != part.number
            or not part.diff or part.diff not in original.diff):
        return initial
    current = part.review_scope.data.get('current_chunk', {}).get('files', [])
    if (not current or not isinstance(current, list)
            or any(not isinstance(path, str) or
                   not (whole := requested_file_diff(original, path, part.head_sha))
                   or whole not in part.diff for path in current)):
        return initial
    entries = []
    for source in sorted(resolver.previously_verified(part),
                         key=lambda item: (-item['bytes'], item['path'])):
        if len(entries) >= min(2, MAX_CONTEXT_FILES - 2):
            break
        if max(context_size(entries + [source])) <= MAX_CONTEXT_SIZE:
            entries.append(source)
    return entries
