"""Sanitize command output tails for results and logs."""

from __future__ import annotations

import re

from cluster.spec import SECRET_PATTERNS

REDACT = "[REDACTED]"

# Additional loose patterns for output sanitization
_OUTPUT_PATTERNS = SECRET_PATTERNS + [
    re.compile(r"(?i)(password|token|secret|api[_-]?key)\s*[=:]\s*\S+"),
]


def sanitize_tail(text: str, max_bytes: int = 2048) -> str:
    if not text:
        return ""
    out = text
    for pat in _OUTPUT_PATTERNS:
        out = pat.sub(REDACT, out)
    encoded = out.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        encoded = encoded[-max_bytes:]
        out = encoded.decode("utf-8", errors="replace")
    return out
