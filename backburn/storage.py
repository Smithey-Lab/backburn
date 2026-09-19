"""Per-user files survive reinstalls and version changes."""

import json
import os
from pathlib import Path


def data_dir() -> Path:
    root = Path(
        os.environ.get(
            "BACKBURN_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "Backburn" / "userdata",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def save_game(sim, name="quicksave.bbsave"):
    path = data_dir() / name
    temporary = path.with_suffix(".tmp")
    sim.save_state(temporary)
    temporary.replace(path)


def save_snapshot(snapshot, name="autosave.bbsave"):
    """Write a snapshot taken with ``Simulation.snapshot()``; safe to call from a thread."""
    from .sim import Simulation

    path = data_dir() / name
    temporary = path.with_suffix(f".{name}.tmp")
    Simulation.write_snapshot(snapshot, temporary)
    temporary.replace(path)
