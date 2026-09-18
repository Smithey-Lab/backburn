"""Frozen entry point. `backburn.exe` with no arguments opens the viewer on the default
scenario; with arguments it behaves exactly like `python -m backburn`.

Paths are resolved relative to the directory you ran it from. A bare scenario name that
isn't found there is looked up in the bundled scenarios folder, so
`backburn.exe view wall_of_fire.json` works from anywhere.
"""
import os
import sys


def _bundled_scenarios() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    d = os.path.join(base, "scenarios")
    return d if os.path.isdir(d) else None


def _resolve(argv: list[str]) -> list[str]:
    bundled = _bundled_scenarios()
    if bundled is None:
        return argv
    out = []
    for a in argv:
        if a.endswith(".json") and not os.path.exists(a):
            cand = os.path.join(bundled, os.path.basename(a))
            if os.path.exists(cand):
                a = cand
        out.append(a)
    return out


from backburn.__main__ import main  # noqa: E402

if __name__ == "__main__":
    argv = _resolve(sys.argv[1:])
    if not argv:
        bundled = _bundled_scenarios()
        argv = ["view"] + ([os.path.join(bundled, "prairie_fire.json")] if bundled else [])
    sys.exit(main(argv))
