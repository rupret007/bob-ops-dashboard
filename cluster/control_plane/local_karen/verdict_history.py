"""Private immutable observations; historical files never grant active authority."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
import stat
import uuid


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Unsafe verdict storage file')
    return info


@contextmanager
def store_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(root)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Unsafe verdict storage directory')
        fcntl.flock(root, fcntl.LOCK_EX)
        yield root
    finally:
        os.close(root)


@contextmanager
def delivery_lock(root):
    fd = os.open('.push.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        _regular(fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(root, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
    try:
        if _regular(fd).st_size > 8 * 1024 * 1024:
            raise ValueError('Oversized historical observation')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = stream.read(8 * 1024 * 1024 + 1)
        if len(value) > 8 * 1024 * 1024:
            raise ValueError('Oversized historical observation')
        return value
    finally:
        os.close(fd)


def archive_observation(root, name, replacement=None):
    """Called under store_lock before any existing observation is lost."""
    if not name or '/' in name or name in ('.', '..'):
        raise ValueError('Invalid observation name')
    try:
        value = _read(root, name)
    except FileNotFoundError:
        return
    if value == replacement:
        return
    try:
        os.mkdir('history', 0o700, dir_fd=root)
    except FileExistsError:
        pass
    history = os.open('history', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    temporary = None
    try:
        info = os.fstat(history)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe verdict history directory')
        target = hashlib.sha256(value).hexdigest() + '.json'
        try:
            existing = _read(history, target)
        except FileNotFoundError:
            temporary = '.archive-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=history)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target, src_dir_fd=history, dst_dir_fd=history)
            except FileExistsError:
                if _read(history, target) != value:
                    raise ValueError('Conflicting historical observation')
        else:
            if existing != value:
                raise ValueError('Conflicting historical observation')
        os.fsync(history)
        os.fsync(root)
    finally:
        try:
            if temporary is not None:
                os.unlink(temporary, dir_fd=history)
        finally:
            os.close(history)
