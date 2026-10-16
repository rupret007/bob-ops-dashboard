"""Worker-owned policy. Changes to these allowlists require code review."""

ALLOWED_REPOS = frozenset({"rupret007/bob-ops-dashboard"})

# Match the entire argv, never just the executable or a command prefix.
ALLOWED_ARGV = {
    "test": frozenset({
        ("python3", "-m", "unittest", "discover", "-s", "cluster/tests", "-v"),
        ("python3", "-m", "unittest", "discover", "-v"),
        ("npm", "test"),
    }),
    "build": frozenset({("npm", "run", "build")}),
    "render": frozenset({("npm", "run", "render")}),
}

# Values are restricted too: no arbitrary data channel or tool configuration.
ALLOWED_ENV = {
    "CI": frozenset({"true", "1"}),
    "LANG": frozenset({"C", "C.UTF-8"}),
    "LC_ALL": frozenset({"C", "C.UTF-8"}),
    "TZ": frozenset({"UTC"}),
}
WORKER_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
MAX_TIMEOUT_S = 7200
MAX_LOAD_CEILING = 32
