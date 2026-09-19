"""Desktop mission preparation.

Shipped missions are authored at desktop scale (``terrain.mode == "world"``) and start with
no response units, so they need no adjustment. ``expanded_scenario`` remains for the
prototype-scale files (the tuning harness and tests): it upsamples the map and applies the
desktop fleet rules so old content still plays in the desktop game.
"""

import numpy as np

from .config import UNITS
from .scenario import Scenario

DESKTOP_MIN_SIDE = 512  # anything smaller is prototype scale and gets expanded


def desktop_scenario(source: Scenario) -> Scenario:
    """The scenario the desktop game should run for ``source``."""
    if source.terrain_mode == "world" or max(source.width, source.height) >= DESKTOP_MIN_SIDE:
        scenario = Scenario.from_dict(source.to_dict())
        scenario.units = [u for u in scenario.units if UNITS[u["type"]].get("is_civilian")]
        if scenario.budget is None:
            scenario.budget = 18000
        if scenario.available_units == []:
            scenario.available_units = None
        return scenario
    return expanded_scenario(source)


def expanded_scenario(source: Scenario, scale: int = 3) -> Scenario:
    """Prototype-scale layouts preserve their geography at a larger scale."""
    scenario = Scenario.from_dict(source.to_dict())
    scenario.variable_wind = True
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
