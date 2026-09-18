"""Regression tests for release safety, replay completeness and mission outcomes."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from backburn.scenario import load_scenario
from backburn.sim import RUNNING, Simulation
from backburn_update import current, install_archive

ROOT = Path(__file__).resolve().parents[1]


def bundle(tmp_path, entries):
    path = tmp_path / "release.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_update_preserves_saves_and_bad_hash_keeps_active_version(tmp_path):
    root = tmp_path / "install"
    (root / "userdata").mkdir(parents=True)
    (root / "userdata/save.bbsave").write_text("user save")
    path, digest = bundle(tmp_path, {"Backburn.exe": b"test executable", "_internal/data": b"data"})
    first = install_archive(path, digest, "v0.3.0", root)
    assert current(root) == first
    with pytest.raises(ValueError, match="checksum"):
        install_archive(path, "0" * 64, "v0.4.0", root)
    assert current(root) == first
    assert (root / "userdata/save.bbsave").read_text() == "user save"
    second = install_archive(path, digest, "v0.4.0", root)
    assert current(root) == second
    assert (root / first["folder"] / "Backburn.exe").exists()


@pytest.mark.parametrize("name", ["../escape.exe", "/absolute.exe", "C:/escape.exe", "..\\escape.exe"])
def test_updater_rejects_archive_traversal(tmp_path, name):
    path, digest = bundle(tmp_path, {"Backburn.exe": b"exe", name: b"bad"})
    with pytest.raises(ValueError, match="Unsafe"):
        install_archive(path, digest, "v0.3.0", tmp_path / "install")
    assert current(tmp_path / "install") is None


def test_updater_rejects_missing_executable(tmp_path):
    path, digest = bundle(tmp_path, {"readme.txt": "incomplete"})
    with pytest.raises(ValueError, match="missing"):
        install_archive(path, digest, "v0.3.0", tmp_path / "install")


def test_replay_includes_commands_at_final_tick(tmp_path):
    sim = Simulation(load_scenario(ROOT / "scenarios/prairie_fire.json"))
    sim.step(5)
    sim.cmd_wind(13, 190)
    sim.cmd_order(1, "MOVE", target=(40, 40))
    path = tmp_path / "replay.json"
    sim.save_replay(path)
    replay = Simulation.replay(path)
    assert replay.grid.wind_speed == 13
    assert replay.world.to_dict() == sim.world.to_dict()
    assert len(replay.log) == len(sim.log)


def test_containment_waits_for_scheduled_ignition():
    sc = load_scenario(ROOT / "scenarios/prairie_fire.json")
    sc.events = [{"at": 50, "ignite": {"x": 30, "y": 30}}]
    sim = Simulation(sc)
    sim.grid.state[:] = 0
    sim.grid.smolder_timer[:] = 0
    sim.step(3)
    assert sim.outcome == RUNNING


def test_hikers_can_be_rescued_in_shipped_mission():
    sim = Simulation(load_scenario(ROOT / "scenarios/stranded_hikers.json"))
    heli = sim.world.units[0]
    for civilian in sim.world.civilians():
        sim.cmd_order(heli.uid, "PICKUP", unit_id=civilian.uid, queue=True)
    sim.cmd_order(heli.uid, "DROPOFF", target=sim.world.safe_zone[:2], queue=True)
    sim.step(600)
    assert sim.civilian_counts()["rescued"] == 4
    assert sim.outcome == "contained"
