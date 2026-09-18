"""Backburn — clean-room wildfire tactics simulation core.

SimulationCore only. No rendering dependencies beyond an optional headless
PNG/GIF exporter (backburn.render) and an optional pygame viewer (backburn.viewer).
"""
from .config import TERRAIN, FIRE, UNITS, TerrainType, MoveClass
from .sim import CONTAINED, FAILED, RUNNING, TIMEOUT, Simulation
from .scenario import Scenario, ScenarioError, load_scenario, save_scenario

__all__ = [
    "TERRAIN", "FIRE", "UNITS", "TerrainType", "MoveClass",
    "Simulation", "Scenario", "ScenarioError", "load_scenario", "save_scenario",
    "RUNNING", "CONTAINED", "FAILED", "TIMEOUT",
]
__version__ = "0.2.0"
