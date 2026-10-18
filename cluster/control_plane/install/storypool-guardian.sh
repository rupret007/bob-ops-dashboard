#!/bin/bash
set -euo pipefail
export PYTHONPATH="__REPO_PATH__${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m cluster.control_plane.guardian --machine mini
