"""Read launcher credentials from a private file outside the published checkout."""
import os
from pathlib import Path
import stat
import sys


def read_private(path):
    path = Path(path).expanduser().resolve()
    root = Path(__file__).resolve().parent.parent
    if path == root or root in path.parents:
        raise ValueError("private configuration must be outside the repository")
    with path.open() as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("private configuration must be owned by you with chmod 600")
        return stream.read()


if __name__ == "__main__":
    try:
        value = read_private(sys.argv[2])
        if sys.argv[1] == "token":
            value = value.rstrip("\r\n")
            if not value.strip() or any(c.isspace() for c in value):
                raise ValueError("LIVE_TOKEN file must contain one non-empty token without whitespace")
            print(value)
    except (OSError, ValueError) as error:
        sys.exit(f"live configuration: {error}")
