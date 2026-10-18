"""Generate and validate least-privilege coordination ACLs without credentials.

Run ``python3 -m cluster.nats_acl --check cluster/nats.conf.template`` in CI,
including after adding a coordination bucket. --write refreshes the generated
block; the broker installer also renders it directly from the registry.
"""
import argparse
import json
import re
from pathlib import Path

from cluster.coordination_buckets import BUCKETS

START = '# BEGIN GENERATED COORD ACL'
END = '# END GENERATED COORD ACL'


def coord_permissions(operator=False):
    publish = []
    for bucket, _ttl in BUCKETS.values():
        stream = 'KV_' + bucket
        publish.extend(('$KV.' + bucket + '.>',
                        '$JS.API.STREAM.INFO.' + stream,
                        '$JS.API.STREAM.MSG.GET.' + stream,
                        '$JS.API.DIRECT.GET.' + stream,
                        '$JS.API.DIRECT.GET.' + stream + '.>'))
        if operator:
            publish.extend(('$JS.API.STREAM.CREATE.' + stream,
                            '$JS.API.STREAM.UPDATE.' + stream))
    user = 'coordop' if operator else 'coord'
    return {'publish': publish, 'subscribe': ['_INBOX_' + user + '.>']}


def render_coord_acl(text):
    blocks = []
    for user in ('coord', 'coordop'):
        permissions = coord_permissions(operator=user == 'coordop')
        blocks.append('''    {
      user: %s
      password: "{{%s_TOKEN}}"
      permissions: {
        publish: %s
        subscribe: %s
      }
    }''' % (user, user.upper(), json.dumps(permissions['publish']),
           json.dumps(permissions['subscribe'])))
    generated = START + '\n' + '\n'.join(blocks) + '\n    ' + END
    pattern = re.escape(START) + r'.*?' + re.escape(END)
    if len(re.findall(pattern, text, flags=re.S)) != 1:
        raise ValueError('Coordination ACL requires exactly one generated block')
    return re.sub(pattern, lambda _: generated, text, flags=re.S)


def verify_coord_acl(text):
    """Fail closed on missing subjects or accidental broader permissions.

    Diagnostics name identities/subjects only, never config or passwords.
    """
    for user in ('coord', 'coordop'):
        matches = list(re.finditer(r'\buser:\s*"?' + user + r'"?(?=\s|,|$)', text))
        if len(matches) != 1:
            raise ValueError('Coordination ACL requires one ' + user + ' identity')
        block = re.split(r'\buser:', text[matches[0].end():], maxsplit=1)[0]
        for action, expected in coord_permissions(user == 'coordop').items():
            found = re.search(action + r':\s*(\[[^\]]*\])', block)
            try:
                actual = json.loads(found.group(1)) if found else []
            except ValueError:
                raise ValueError('Invalid coordination ACL for ' + user) from None
            if set(actual) != set(expected):
                missing = sorted(set(expected) - set(actual))
                raise ValueError('Coordination ACL drift for ' + user + ' ' + action
                                 + '; missing subjects: ' + ', '.join(missing)
                                 + '; regenerate with cluster.nats_acl --write')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', type=Path)
    modes.add_argument('--write', type=Path)
    args = parser.parse_args()
    path = args.check or args.write
    try:
        text = path.read_text()
        if args.write:
            text = render_coord_acl(text)
            path.write_text(text)
        verify_coord_acl(text)
    except (OSError, ValueError) as exc:
        # Never emit OSError paths or credential-bearing config contents.
        raise SystemExit(str(exc) if isinstance(exc, ValueError)
                         else 'Coordination ACL file unavailable') from None


if __name__ == '__main__':
    main()
