"""Desktop mission layouts preserve the prototype geography at a larger scale."""

import numpy as np

from .scenario import Scenario


def expanded_scenario(source: Scenario, scale: int = 2) -> Scenario:
    scenario = Scenario.from_dict(source.to_dict())
    scenario.terrain_mode = "grid"
    scenario.terrain_grid = np.repeat(np.repeat(source.build_terrain(), scale, axis=0), scale, axis=1)
    scenario.height, scenario.width = scenario.terrain_grid.shape
    elevation = source.build_elevation()
    if elevation is not None:
        scenario.elevation = {
            "mode": "grid",
            "rows": np.repeat(np.repeat(elevation, scale, axis=0), scale, axis=1).tolist(),
        }
    scenario.airbase = tuple(v * scale for v in source.airbase)
    if source.staging:
        scenario.staging = tuple(v * scale for v in source.staging)
    if source.safe_zone:
        scenario.safe_zone = tuple(v * scale for v in source.safe_zone)
    scenario.units = [{**u, "x": u["x"] * scale, "y": u["y"] * scale} for u in source.units]
    scenario.ignitions = [{**ig, "x": ig["x"] * scale, "y": ig["y"] * scale} for ig in source.ignitions]
    scenario.events = []
    for event in source.events:
        event = dict(event)
        for key in ("ignite", "spawn"):
            if key in event:
                event[key] = {k: v * scale if k in ("x", "y") else v for k, v in event[key].items()}
        scenario.events.append(event)
    return scenario
