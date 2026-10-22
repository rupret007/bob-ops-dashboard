"""Bound local Git pipe reads before buffering, under one fixed call deadline."""
import os
import selectors
import subprocess
import time


class GitReadError(RuntimeError):
    """Fixed diagnostics; never include child output or repository contents."""


def capture(command, *, env, data, limit, timeout=30):
    if (type(limit) is not int or not 0 < limit <= 64*1024*1024+10000*128
            or type(timeout) not in (int, float) or not 0 < timeout <= 30
            or data is not None and (type(data) is not bytes or len(data) > 10000*41)):
        raise GitReadError('Release object read bound is invalid')
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, env=env,
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, errors = bytearray(), bytearray()
    pending = memoryview(data) if data is not None else None
    try:
        with selectors.DefaultSelector() as observer:
            for stream, value in ((process.stdout, (output, limit)),
                                  (process.stderr, (errors, 16384))):
                os.set_blocking(stream.fileno(), False)
                observer.register(stream, selectors.EVENT_READ, value)
            if process.stdin is not None:
                if pending:
                    os.set_blocking(process.stdin.fileno(), False)
                    observer.register(process.stdin, selectors.EVENT_WRITE, None)
                else:
                    process.stdin.close()
            while observer.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GitReadError('Release object read deadline exceeded')
                for key, event in observer.select(remaining):
                    stream = key.fileobj
                    if key.data is None:
                        try:
                            count = os.write(stream.fileno(), pending[:65536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            pending = pending[len(pending):]
                        else:
                            pending = pending[count:]
                        if not pending:
                            observer.unregister(stream)
                            stream.close()
                        continue
                    destination, bound = key.data
                    try:
                        chunk = os.read(stream.fileno(), min(65536, bound-len(destination)+1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        observer.unregister(stream)
                        stream.close()
                        continue
                    if len(destination) + len(chunk) > bound:
                        raise GitReadError('Release object output exceeds limit')
                    destination.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GitReadError('Release object read deadline exceeded')
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise GitReadError('Release object read deadline exceeded') from None
        return code, bytes(output), bytes(errors)
    finally:
        # Popen owns this unreaped child. Never signal another process/group.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
