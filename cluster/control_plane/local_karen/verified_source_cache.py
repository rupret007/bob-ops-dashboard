"""Internal immutable source snapshots; populated only after fetch verification."""


class VerifiedSourceSnapshots:
    def __init__(self, identity, number):
        self._identity = identity
        self._number = number
        self._entries = {}

    def remember(self, source):
        # Values are immutable strings/integers produced by the source verifier.
        # Never retain the mutable dict returned to a caller.
        self._entries[source['path']] = tuple(source.items())

    def for_pr(self, pr):
        if (type(self._number) is not int or self._number <= 0
                or (pr.owner, pr.repo, pr.head_sha) != self._identity
                or getattr(pr, 'number', None) != self._number):
            return []
        return [dict(snapshot) for snapshot in self._entries.values()]
