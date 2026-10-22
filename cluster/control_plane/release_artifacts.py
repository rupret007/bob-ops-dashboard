"""Prepare fixed source releases without changing running code or selecting one.

The normal updater must supply the approved commit and bind the resulting
identity to its transaction. This module grants no admission, execution, health
or rollback authority. It does not change native jobs, config, data or locks
outside its private artifact store. Read-only modes prevent accidental writes;
they are not a sandbox against other processes running as the same user.
"""
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile

MAX_FILES = 10000
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


class ArtifactError(RuntimeError):
    """Fixed diagnostics; no source contents or command output are disclosed."""


def _require(value, reason):
    if not value: raise ArtifactError(reason)


def _hex(value, size):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(size)+'}', value) is not None


@dataclass(frozen=True)
class Release:
    head: str
    tree: str
    manifest_sha256: str

    def validate(self):
        _require(_hex(self.head, 40) and _hex(self.tree, 40)
                 and _hex(self.manifest_sha256, 64), 'Release identity is invalid')


def _held(allowed):
    _require(callable(allowed) and allowed() is True, 'Artifact preparation is held')


def _path(value):
    _require(type(value) is str and value and '\\' not in value
             and not any(c in value for c in ('\0', '\n', '\r')),
             'Release path is invalid')
    path = PurePosixPath(value)
    _require(not path.is_absolute() and str(path) == value
             and not any(p in ('.', '..', '.git') for p in path.parts),
             'Release path escapes its source directory')
    return path


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(path, mode):
    _require(path.is_absolute() and path.resolve() == path, 'Artifact directory is linked')
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == mode, 'Artifact directory is not owned')
    return _stamp(info)


def _read(path, limit, mode):
    _require(path.is_absolute() and path.resolve() == path, 'Artifact file is linked')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == mode
                 and info.st_size <= limit, 'Artifact file ownership differs')
        raw = stream.read(limit+1)
        _require(len(raw) == info.st_size and _stamp(os.fstat(stream.fileno())) == _stamp(info)
                 and _stamp(path.lstat()) == _stamp(info), 'Artifact file changed during read')
    return raw, _stamp(info)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Artifact manifest repeats a field')
        result[key] = value
    return result


def _git(repo, args, *, data=None, missing_config=False):
    # Object reads do not apply checkout filters, archive substitutions or
    # hooks. Ignore replace refs and inherited configuration injection.
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1',
               GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull,
        '-C', str(repo), *args], input=data, env=env, capture_output=True, timeout=30)
    absent = missing_config and result.returncode == 1 and not result.stdout
    _require((result.returncode == 0 or absent) and not result.stderr.strip(), 'Release object read failed')
    return result.stdout


def _objects(repo, head):
    # Older Git versions ignore GIT_NO_LAZY_FETCH. Reject every documented
    # promisor configuration before an object lookup can trigger a fetch.
    _require(not _git(repo, ['config', '--name-only', '--get-regexp',
        r'^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$'],
        missing_config=True), 'Partial repository object reads are unsupported')
    _require(_git(repo, ['rev-parse', '--verify', head+'^{commit}']).decode().strip() == head,
             'Release head is not its exact commit')
    tree = _git(repo, ['rev-parse', head+'^{tree}']).decode().strip()
    _require(_hex(tree, 40), 'Release tree is invalid')
    listing = _git(repo, ['ls-tree', '-r', '-l', '-z', '--full-tree', head])
    _require(0 < len(listing) <= 4*1024*1024 and listing.endswith(b'\0'),
             'Release inventory exceeds limit')
    files, total = {}, 0
    for row in listing[:-1].split(b'\0'):
        metadata, path = row.split(b'\t', 1)
        mode, kind, oid, size = metadata.decode('ascii').split()
        path = path.decode('utf-8'); _path(path)
        _require(mode in ('100644', '100755') and kind == 'blob' and _hex(oid, 40)
                 and size.isdecimal() and int(size) <= MAX_FILE and path not in files,
                 'Release inventory contains unsupported objects')
        files[path] = dict(mode=mode, oid=oid, bytes=int(size))
        total += int(size)
        _require(len(files) <= MAX_FILES and total <= MAX_TOTAL, 'Release inventory exceeds limit')
    _require(bool(files), 'Release inventory is empty')
    return tree, files


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _blobs(files, output):
    stream = io.BytesIO(output)
    for name, metadata in files.items():
        _require(stream.readline(128) ==
            (metadata['oid']+' blob '+str(metadata['bytes'])+'\n').encode(), 'Release blob framing differs')
        data = stream.read(metadata['bytes'])
        _require(stream.read(1) == b'\n' and len(data) == metadata['bytes']
                 and hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == metadata['oid'],
                 'Release blob content differs')
        yield name, metadata, data
    _require(stream.read(1) == b'', 'Release blob output has extra entries')


def _verify(directory, identity, allowed):
    identity.validate(); _held(allowed)
    # Keep the private publication envelope writable for rename on macOS.
    # The complete source tree and manifest remain read-only on both hosts.
    roots = {directory:_directory(directory, 0o700)}
    raw, manifest_stamp = _read(directory/'manifest.json', 4*1024*1024, 0o444)
    _require(hashlib.sha256(raw).hexdigest() == identity.manifest_sha256,
             'Artifact manifest differs from the supplied identity')
    value = json.loads(raw, object_pairs_hook=_unique)
    _require(type(value) is dict and set(value) == {'version','head','tree','files'}
             and type(value['version']) is int and value['version'] == 1
             and value['head'] == identity.head and value['tree'] == identity.tree
             and type(value['files']) is dict and 0 < len(value['files']) <= MAX_FILES,
             'Artifact manifest schema differs')
    _require({p.name for p in directory.iterdir()} == {'manifest.json','source'},
             'Artifact has undeclared entries')
    source = directory/'source'; roots[source] = _directory(source, 0o555)
    expected_dirs, total, observed = {source}, 0, {}
    for name, metadata in value['files'].items():
        path = source/_path(name)
        _require(type(metadata) is dict and set(metadata) == {'mode','oid','bytes','sha256'}
                 and metadata['mode'] in ('100644','100755') and _hex(metadata['oid'], 40)
                 and type(metadata['bytes']) is int and 0 <= metadata['bytes'] <= MAX_FILE
                 and _hex(metadata['sha256'], 64), 'Artifact file metadata differs')
        expected_dirs.update(p for p in path.parents if p == source or source in p.parents)
        for parent in reversed(path.parents):
            if parent in expected_dirs and parent not in roots:
                roots[parent] = _directory(parent, 0o555)
        total += metadata['bytes']; _require(total <= MAX_TOTAL, 'Artifact inventory exceeds limit')
        mode = 0o555 if metadata['mode'] == '100755' else 0o444
        data, stamp = _read(path, MAX_FILE, mode)
        _require(len(data) == metadata['bytes'] and hashlib.sha256(data).hexdigest() == metadata['sha256']
                 and hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == metadata['oid'],
                 'Artifact source differs from its Git object')
        observed[path] = stamp
    actual_dirs, actual_files = set(), set()
    for base, dirs, files in os.walk(source, followlinks=False):
        base = Path(base); actual_dirs.add(base)
        _require(base in roots and _directory(base, 0o555) == roots[base],
                 'Artifact source directory changed')
        for name in dirs: _directory(base/name, 0o555)
        actual_files.update(base/name for name in files)
        _require(len(actual_dirs) <= MAX_FILES+1 and len(actual_files) <= MAX_FILES,
                 'Artifact has too many entries')
    _require(actual_dirs == expected_dirs and actual_files == set(observed),
             'Artifact source has undeclared entries')
    _held(allowed)
    _require(all(_stamp(path.lstat()) == stamp for path,stamp in {**roots,**observed}.items())
             and _stamp((directory/'manifest.json').lstat()) == manifest_stamp,
             'Artifact changed during verification')
    return source


def verify(store, identity, *, allowed):
    """Verify against a caller-held identity, never a self-authorizing manifest."""
    identity.validate(); store = Path(store)
    try:
        _directory(store, 0o700)
        return _verify(store/identity.head, identity, allowed)
    except (OSError, ValueError, UnicodeError):
        raise ArtifactError('Artifact is unavailable or invalid') from None


def build(repo, head, store, *, allowed):
    """Publish one complete fixed source tree; retain interrupted stages.

    Failure never removes or changes an existing release. Leftover private
    stages are evidence for attributed cleanup, never runnable selections.
    """
    _require(_hex(head, 40), 'Release head is invalid'); _held(allowed)
    repo, store = Path(repo), Path(store)
    _require(repo.is_absolute() and repo.resolve() == repo, 'Release repository is linked')
    _require(store.is_absolute() and store.resolve() == store, 'Artifact store is linked')
    store.mkdir(mode=0o700, parents=True, exist_ok=True); _directory(store, 0o700)
    fd = os.open(store/'.publish.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a') as lock:
        info = os.fstat(lock.fileno())
        _require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                 and info.st_uid == os.getuid() and info.st_nlink == 1
                 and _stamp(info) == _stamp((store/'.publish.lock').lstat()), 'Artifact publisher lock differs')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ArtifactError('Another artifact publisher owns this store') from None
        tree, files = _objects(repo, head); _held(allowed)
        # The sizes above bound this local Git object's complete batch output.
        output = _git(repo, ['cat-file','--batch'],
                      data=b''.join(v['oid'].encode()+b'\n' for v in files.values()))
        _require(len(output) <= MAX_TOTAL+MAX_FILES*128, 'Release blob output exceeds limit')
        for name, metadata, data in _blobs(files, output):
            metadata['sha256'] = hashlib.sha256(data).hexdigest()
        manifest = json.dumps(dict(version=1,head=head,tree=tree,files=files),
                              sort_keys=True,separators=(',',':')).encode()+b'\n'
        identity = Release(head, tree, hashlib.sha256(manifest).hexdigest())
        target = store/head
        if os.path.lexists(target):
            _verify(target, identity, allowed)
            return identity
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=store))
        source = stage/'source'; source.mkdir(mode=0o700)
        for index,(name,metadata,data) in enumerate(_blobs(files, output)):
            if index % 32 == 0: _held(allowed)
            path = source/name; path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open('xb') as out:
                out.write(data); out.flush()
                os.fchmod(out.fileno(), 0o555 if metadata['mode'] == '100755' else 0o444)
                os.fsync(out.fileno())
        with (stage/'manifest.json').open('xb') as out:
            out.write(manifest); out.flush(); os.fchmod(out.fileno(),0o444); os.fsync(out.fileno())
        for base,dirs,_ in os.walk(stage,topdown=False):
            if Path(base) != stage: os.chmod(base,0o555)
            _sync(base)
        _verify(stage, identity, allowed); _held(allowed)
        _require(not os.path.lexists(target), 'Artifact publication target appeared')
        _require(_stamp(os.fstat(lock.fileno())) == _stamp((store/'.publish.lock').lstat()),
                 'Artifact publisher lock changed before publication')
        os.rename(stage,target); _sync(store)
        verify(store,identity,allowed=allowed)
        return identity
