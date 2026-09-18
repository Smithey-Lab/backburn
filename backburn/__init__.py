"""Backburn — clean-room wildfire tactics simulation core.

SimulationCore only. No rendering dependencies beyond an optional headless
PNG/GIF exporter (backburn.render) and an optional pygame viewer (backburn.viewer).
"""
from .config import TERRAIN, FIRE, UNITS, TerrainType, MoveClass
from .sim import Simulation
from .scenario import Scenario, load_scenario, save_scenario

__all__ = [
    "TERRAIN", "FIRE", "UNITS", "TerrainType", "MoveClass",
    "Simulation", "Scenario", "load_scenario", "save_scenario",
]
__version__ = "0.1.0"
