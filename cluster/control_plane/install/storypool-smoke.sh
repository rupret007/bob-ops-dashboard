#!/bin/bash
# Default verifies the latest real job; --repo OWNER/REPO --pr N executes a real fix.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m cluster.control_plane.smoke "$@"
