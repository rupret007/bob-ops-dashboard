"""Authenticate an approved Git commit and its complete canonical file tree."""
import hashlib
from pathlib import PurePosixPath


class ObjectIdentityError(ValueError):
    """Fixed diagnostics; no object contents are disclosed."""


def _require(value):
    if not value:
        raise ObjectIdentityError('Release commit or tree identity differs')


def _oid(kind, body):
    return hashlib.sha1(kind+b' '+str(len(body)).encode()+b'\0'+body).hexdigest()


def authenticate_commit(head, tree, body):
    _require(_oid(b'commit', body) == head)
    _require(body.partition(b'\n')[0] == b'tree '+tree.encode('ascii'))


def authenticate_tree(tree, files, *, maximum):
    # Rebuild every directory from the already bounded, validated inventory.
    # Bottom-up hashing also authenticates descendant tree objects: checking
    # blob hashes alone cannot connect their names/modes to the approved root.
    root = {}
    nodes = [root]
    for path, metadata in files.items():
        parts = PurePosixPath(path).parts
        node = root
        for name in parts[:-1]:
            if name not in node:
                child = {}
                node[name] = ('40000', child)
                nodes.append(child)
                _require(len(nodes) <= maximum+1)
            mode, child = node[name]
            _require(mode == '40000')
            node = child
        _require(parts[-1] not in node)
        node[parts[-1]] = (metadata['mode'], metadata['oid'])
    hashes = {}
    for node in reversed(nodes):
        ordered = sorted(node.items(), key=lambda item:
            item[0].encode('utf-8')+(b'/' if item[1][0] == '40000' else b''))
        body = b''.join(mode.encode('ascii')+b' '+name.encode('utf-8')+b'\0'
            +bytes.fromhex(hashes[id(value)] if mode == '40000' else value)
            for name, (mode, value) in ordered)
        hashes[id(node)] = _oid(b'tree', body)
    _require(hashes[id(root)] == tree)
