"""Allow python -m cluster.control_plane.dashboard."""
from .server import main

if __name__ == '__main__':
    raise SystemExit(main())
