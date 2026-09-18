# Contributing

This is a personal clean-room project. If you're contributing (human or agent):

- Read `DECISIONS.md` first. Change a decision there before changing code that depends on it.
- Numbers go in `backburn/data/*.json`, never in code. Run `python tools/gen_tables.py` after.
- Every behaviour gets a test in `tests/` that asserts a relationship, not a constant.
- Keep the sim (`fire.py`, `units.py`, `sim.py`, `scenario.py`, `pathfinding.py`) free of
  rendering imports.
- Determinism rules in `docs/DEVELOPMENT.md` are hard rules.
- No original FireJumpers code, art, audio, or extracted assets. Reference screenshots are
  for comparison only. Label uncertain behaviour as documented / inferred / new.
