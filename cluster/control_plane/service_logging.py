"""Shared bounded JSON diagnostics. Never pass prompts, diffs, or CLI output here."""
from __future__ import annotations

import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from cluster.sanitize import sanitize_tail

_context = contextvars.ContextVar('storypool_log_context', default={})
_SENSITIVE = re.compile(r'(?i)(secret|password|authorization|api.?key|access.?token|^token$)')


def redact(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if _SENSITIVE.search(str(k)) else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(x) for x in value]
    if isinstance(value, str):
        value = re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                       '[REDACTED]', value)
        value = re.sub(r'''(?i)(?:password|token|secret|api[_-]?key)\s*[=:]\s*(?:"[^"]*"|'[^']*')''',
                       '[REDACTED]', value)
        value = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', value)
        value = re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[REDACTED]@', value)
        value = re.sub(r'xox[baprs]-[\w-]+', '[REDACTED]', value)
        return sanitize_tail(value, max_bytes=8192)
    return value


def bind_context(**fields):
    return _context.set(dict(fields))


def reset_context(token):
    _context.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def format(self, record):
        fields = dict(_context.get())
        fields.update(getattr(record, 'fields', {}))
        data = dict(timestamp=datetime.fromtimestamp(record.created, ZoneInfo('America/Chicago')).isoformat(),
                    service=self.service, machine=os.environ.get('STORYPOOL_MACHINE') or
                    ('mini' if sys.platform == 'darwin' else 'nucbox'), level=record.levelname,
                    job_id='', pr_number=None, repo='', sha='', correlation_id='')
        data.update(fields)
        data['event'] = record.getMessage()
        if record.exc_info:
            # Complete frame chain, without locals, source lines, or exception text
            # (CalledProcessError/TimeoutExpired can contain a prompt or CLI output).
            data['exception_type'] = record.exc_info[0].__name__
            data['traceback'] = [dict(file=f.filename, line=f.lineno, function=f.name)
                                 for f in traceback.extract_tb(record.exc_info[2])]
        return json.dumps(redact(data), ensure_ascii=True, default=str)


class DiagnosticWriteError(OSError):
    """A handler failed; its exception payload must not reach stderr."""


class _PrivateHandlerErrors:
    def handleError(self, record):
        # logging's default fallback prints chained exceptions and record arguments.
        # Report a fixed failure to event(), regardless of logging.raiseExceptions.
        raise DiagnosticWriteError('Structured diagnostic write failed') from None


class PrivateStreamHandler(_PrivateHandlerErrors, logging.StreamHandler):
    pass


class RetainedRotatingHandler(_PrivateHandlerErrors, RotatingFileHandler):
    """Size cap plus seven-day expiry for rotated files; one writer per job."""
    def doRollover(self):
        super().doRollover()
        cutoff = time.time() - 7 * 86400
        for path in Path(self.baseFilename).parent.glob(Path(self.baseFilename).name + '.*'):
            if path.suffix[1:].isdigit() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)


def event(service, name, level=logging.INFO, exc_info=None, *, raise_on_error=False, **fields):
    """Handler failures are nonfatal unless a guarded caller opts into propagation."""
    root = Path(os.environ.get('STORYPOOL_LOG_DIR') or
                str(Path(os.environ.get('STORYPOOL_DATA_DIR', str(Path.home() / '.local/share/storypool'))) / 'logs'))
    root.mkdir(parents=True, exist_ok=True)
    job_id = _context.get().get('job_id', '')
    leaf = re.sub(r'[^A-Za-z0-9_.-]', '_', service + ('-' + job_id if job_id else ''))
    path = root / (leaf + '.jsonl')
    logger = logging.getLogger('storypool.structured.' + str(path))
    logger.propagate = False
    logger.setLevel(getattr(logging, os.environ.get('STORYPOOL_LOG_LEVEL', 'INFO').upper(), logging.INFO))
    if not logger.handlers:
        cutoff = time.time() - 7 * 86400
        for old in root.glob(service + '-*.jsonl*'):
            if old.is_file() and old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        formatter = JsonFormatter(service)
        handler = RetainedRotatingHandler(path, maxBytes=5 * 1024 * 1024, backupCount=7, encoding='utf-8')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        stream = PrivateStreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    try:
        logger.log(level, name, extra={'fields': fields}, exc_info=exc_info)
    except DiagnosticWriteError:
        if raise_on_error:
            raise


def failing_tests(output):
    """Extract identifiers only: no assertion text, parameter values or output tails."""
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    names = []
    for line in (output or '').splitlines():
        match = re.match(r'^(?:FAILED|ERROR)\s+([A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_]+)*)', line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names[:50]


def failure_reason(exc, *, stage=None):
    """An actionable summary without repr(exc), provider argv, or captured output."""
    if stage is not None:
        from .retry_policy import classify_failure, FAILURE_REASONS, REASONS
        code, _ = classify_failure(exc, stage)
        return REASONS[code] if code in REASONS else FAILURE_REASONS[code]
    import subprocess
    if isinstance(exc, FileNotFoundError):
        return 'Required executable or file is missing: ' + Path(exc.filename or 'unknown').name
    if isinstance(exc, subprocess.CalledProcessError):
        command = exc.cmd[0] if isinstance(exc.cmd, (list, tuple)) and exc.cmd else 'command'
        return '{} exited with code {}; see command_failed event'.format(Path(command).name, exc.returncode)
    if isinstance(exc, subprocess.TimeoutExpired):
        return 'Command exceeded its timeout; see command_failed event'
    # Runtime errors in this supervisor have fixed messages. Do not copy arbitrary
    # exception messages, which may carry credentials from libraries or providers.
    return type(exc).__name__ + ': inspect structured traceback and preceding command events'
