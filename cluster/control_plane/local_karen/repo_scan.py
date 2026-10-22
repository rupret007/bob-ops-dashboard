"""Bound scheduled discovery without granting authority to cached metadata."""
import bisect
import json
import os
from pathlib import Path
import re
import tempfile

MAX_REPOS_PER_CYCLE = 4
REPO = re.compile(r'[A-Za-z0-9-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+')


class RepoScanError(OSError):
    pass


class RepoScan:
    def __init__(self, config, repos):
        self.path = config.get_state_dir() / 'review-repo-scan.json'
        self.observed_repos = set()
        self.deferred_after_review = False
        if any(not isinstance(repo, str) or not REPO.fullmatch(repo) for repo in repos):
            raise RepoScanError('Invalid review scan plan')
        ordered = sorted(set(repos))
        last = None
        try:
            if self.path.stat().st_size > 4096:
                raise RepoScanError('Invalid review scan state')
            data = json.loads(self.path.read_text())
            if (not isinstance(data, dict) or set(data) != {'version', 'last_repo'}
                    or type(data['version']) is not int or data['version'] != 1
                    or not isinstance(data['last_repo'], str) or not REPO.fullmatch(data['last_repo'])):
                raise RepoScanError('Invalid review scan state')
            last = data['last_repo']
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, OSError) as exc:
            raise RepoScanError('Review scan state unavailable') from None
        start = bisect.bisect(ordered, last) if last is not None else 0
        rotated = ordered[start:] + ordered[:start]
        self.repos = rotated[:MAX_REPOS_PER_CYCLE]

    def observed(self, repo):
        """Advance only after the repo was completely observed (or blocked)."""
        if repo not in self.repos:
            raise RepoScanError('Invalid review scan completion')
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(dir=str(self.path.parent), prefix='.review-repo-scan-')
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'last_repo': repo}, stream)
            os.replace(temporary, self.path)
            self.observed_repos.add(repo)
        except OSError:
            raise RepoScanError('Review scan state unavailable') from None
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
