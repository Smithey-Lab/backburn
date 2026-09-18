"""Windowed entry point with persistent crash diagnostics."""

import sys
import traceback

from backburn.game import main
from backburn.storage import data_dir

try:
    main()
except Exception:
    (data_dir() / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
    if sys.stderr:
        traceback.print_exc()
    raise
