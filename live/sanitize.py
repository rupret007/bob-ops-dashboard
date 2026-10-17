"""Strip secrets and PII from strings before they leave the aggregator."""
from __future__ import annotations
import re

_MAX_LEN = 120

_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS]"),
    (re.compile(r"xox[abpr]-[0-9A-Za-z-]+"), "[REDACTED_SLACK]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED_SK]"),
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[REDACTED_GH]"),
    (re.compile(r"gho_[A-Za-z0-9]{36,}"), "[REDACTED_GH]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "[REDACTED_GH]"),
    (
        re.compile(
            r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    (
        re.compile(
            r"(?:\+1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}"
        ),
        "[REDACTED_PHONE]",
    ),
    (re.compile(r"/Users/[^/\s]+"), "/Users/[user]"),
    (re.compile(r"/home/[^/\s]+"), "/home/[user]"),
]


def sanitize(value):
    """Return a sanitized string (or pass through non-strings as str)."""
    if value is None:
        return ""
    text = str(value)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    if len(text) > _MAX_LEN:
        text = text[: _MAX_LEN - 1] + "…"
    return text


def sanitize_obj(obj):
    """Recursively sanitize strings in dicts/lists."""
    if isinstance(obj, dict):
        return {sanitize(k): sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_obj(v) for v in obj]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return sanitize(obj)
