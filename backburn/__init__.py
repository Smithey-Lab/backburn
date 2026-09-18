"""Backburn — clean-room wildfire tactics simulation core.

SimulationCore only. No rendering dependencies beyond an optional headless
PNG/GIF exporter (backburn.render) and an optional pygame viewer (backburn.viewer).
"""

from .config import FIRE, TERRAIN, UNITS, MoveClass, TerrainType
from .scenario import Scenario, ScenarioError, load_scenario, save_scenario
from .sim import CONTAINED, FAILED, RUNNING, TIMEOUT, Simulation

__all__ = [
    "TERRAIN",
    "FIRE",
    "UNITS",
    "TerrainType",
    "MoveClass",
    "Simulation",
    "Scenario",
    "ScenarioError",
    "load_scenario",
    "save_scenario",
    "RUNNING",
    "CONTAINED",
    "FAILED",
    "TIMEOUT",
]
__version__ = "0.3.2"
