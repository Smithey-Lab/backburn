"""Desktop mission layouts preserve the prototype geography at a larger scale."""

import numpy as np

from .config import UNITS
from .scenario import Scenario


def expanded_scenario(source: Scenario, scale: int = 3) -> Scenario:
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
    scenario.units = [u for u in scenario.units if UNITS[u["type"]].get("is_civilian")]
    scenario.budget = source.budget if source.budget is not None else 18000
    if scenario.available_units == []:
        scenario.available_units = None
    scenario.briefing = source.briefing.replace(
        "No budget: every unit you have is already on scene.", "Choose your response fleet within the budget."
    )
    scenario.briefing += (
        " Start with no crews or aircraft. Open Buy units to assemble your fleet before resuming."
    )
    return scenario
