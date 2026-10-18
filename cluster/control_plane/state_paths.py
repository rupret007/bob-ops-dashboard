"""Keep operational state outside the checkout served by GitHub Pages."""

from pathlib import Path


def private_state_dir(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    checkout = Path(__file__).resolve().parents[2]
    if path == checkout or checkout in path.parents:
        raise ValueError("control plane state must be outside the published checkout")
    return path
