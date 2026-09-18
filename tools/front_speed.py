"""Print fire front speeds per fuel type — the number to look at after changing constants.

python tools/front_speed.py [wind_mps]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from backburn.config import TerrainType as T
from backburn.fire import FireGrid

wind = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
print(f"{'fuel':<14}{'downwind':>10}{'upwind':>9}{'no wind':>9}   cells/s after 120 s (seed 3)")
for tt in (T.GRASS, T.SHRUB, T.FOREST, T.DENSE_FOREST):
    out = []
    for w in (wind, 0.0):
        g = FireGrid(np.full((128, 128), int(tt), np.uint8), seed=3)
        g.set_wind(w, 90)
        g.ignite(20, 64, 1)
        for _ in range(120):
            g.step()
        xs = np.nonzero(g.state > 0)[1]
        out.append(((xs.max() - 20) / 120, (20 - xs.min()) / 120))
    print(f"{tt.name:<14}{out[0][0]:>10.2f}{out[0][1]:>9.2f}{out[1][0]:>9.2f}")
