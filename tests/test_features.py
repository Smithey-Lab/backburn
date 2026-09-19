"""Feature tests: civilians & rescue, budget & dispatch, events, outcomes & scoring,
ember spotting, slope, savegames, scenario validation, CLI. Run: pytest -q"""

import json
from pathlib import Path

import numpy as np
import pytest

from backburn import CONTAINED, FAILED, RUNNING, TIMEOUT, Scenario, ScenarioError, Simulation, load_scenario
from backburn.config import FIRE, UNITS
from backburn.config import TerrainType as T
from backburn.fire import FireGrid
from backburn.scenario import generate_elevation, validate
from backburn.units import DROPOFF, PICKUP, SAFE

FIXTURES = Path(__file__).resolve().parent / "fixtures"  # v0.6 prototype-scale scenarios

ROOT = Path(__file__).resolve().parents[1]


def flat(tt, h=64, w=64):
    return np.full((h, w), int(tt), np.uint8)


def rescue_scenario():
    t = flat(T.GRASS, 80, 80)
    t[:, 0:4] = int(T.WATER)
    return Scenario(
        name="rescue",
        width=80,
        height=80,
        terrain_mode="grid",
        terrain_grid=t,
        wind_speed=6,
        wind_bearing=90,
        airbase=(5, 5),
        staging=(6, 40),
        safe_zone=(5, 10, 4),
        units=[
            {"type": "HELICOPTER", "x": 5, "y": 5},
            {"type": "CIVILIAN", "x": 30, "y": 30},
            {"type": "CIVILIAN", "x": 31, "y": 31},
        ],
        ignitions=[{"x": 12, "y": 30, "radius": 1}],
        duration=900,
        objectives={"rescue_all_civilians": True},
    )


# ---- civilians --------------------------------------------------------------------


def test_helicopter_rescues_civilians():
    sim = Simulation(rescue_scenario())
    heli, c1, c2 = sim.world.units[:3]
    assert sim.cmd_order(heli.uid, PICKUP, unit_id=c1.uid)
    assert sim.cmd_order(heli.uid, PICKUP, unit_id=c2.uid, queue=True)
    assert sim.cmd_order(heli.uid, DROPOFF, target=(5, 10), queue=True)
    sim.step(120)
    civ = sim.civilian_counts()
    assert civ["rescued"] == 2 and civ["lost"] == 0
    assert c1.state == SAFE and c2.state == SAFE


def test_civilians_flee_and_can_die():
    t = flat(T.GRASS, 40, 40)
    sc = Scenario(
        width=40,
        height=40,
        terrain_mode="grid",
        terrain_grid=t,
        wind_speed=8,
        wind_bearing=90,
        units=[{"type": "CIVILIAN", "x": 20, "y": 20}],
        ignitions=[{"x": 10, "y": 20, "radius": 2}],
    )
    sim = Simulation(sc)
    c = sim.world.units[0]
    x0 = c.x
    sim.step(40)
    assert c.x > x0 or not c.alive  # ran downwind/away, or was caught
    sim.step(400)
    # With no safe zone on an all-grass map the civilian is eventually caught or the fire burns out.
    assert c.state in ("LOST", "IDLE", "FLEEING")


def test_orders_are_refused_for_civilians():
    sim = Simulation(rescue_scenario())
    civ = sim.world.units[1]
    assert sim.cmd_order(civ.uid, "MOVE", target=(1, 1)) is False


# ---- budget, dispatch, events ------------------------------------------------------


def test_budget_blocks_purchase_and_dispatch_delays_arrival():
    sc = rescue_scenario()
    sc.budget = 1000
    sc.units = []
    sim = Simulation(sc)
    assert sim.cmd_spawn("HELICOPTER") is False  # 2500 > 1000
    assert sim.cmd_spawn("CUT_TEAM") is True  # 300
    assert len(sim.world.units) == 0 and len(sim.world.pending) == 1
    sim.step(int(UNITS["CUT_TEAM"]["arrival_seconds"]) + 1)
    assert len(sim.world.units) == 1 and sim.world.units[0].utype == "CUT_TEAM"
    assert sim.spent == 300


def test_unavailable_unit_is_refused():
    sc = rescue_scenario()
    sc.available_units = ["ENGINE"]
    sim = Simulation(sc)
    assert sim.cmd_spawn("HELICOPTER") is False
    assert sim.cmd_spawn("ENGINE") is True


def test_events_fire_once_at_their_time():
    sc = rescue_scenario()
    sc.units = []
    sc.events = [
        {"at": 30, "wind": {"speed": 12, "bearing": 180}},
        {"at": 50, "message": "hello"},
        {"at": 70, "spawn": {"type": "ENGINE"}},
        {"at": 90, "ignite": {"x": 60, "y": 60}},
    ]
    sim = Simulation(sc)
    sim.step(29)
    assert sim.grid.wind_speed == 6
    sim.step(2)
    assert sim.grid.wind_speed == 12 and sim.grid.wind_bearing == 180
    sim.step(70)
    kinds = [m.kind for m in sim.messages]
    assert kinds.count("event") == 4
    assert any("hello" in m.text for m in sim.messages)
    assert sim.grid.state[60, 60] != 0


# ---- outcomes & scoring ------------------------------------------------------------


def test_contained_outcome_and_score():
    t = flat(T.GRASS, 40, 40)
    t[:, 20] = int(T.WATER)  # small burnable pocket on the left
    t[:, 0:5] = int(T.WATER)
    sc = Scenario(
        width=40,
        height=40,
        terrain_mode="grid",
        terrain_grid=t,
        ignitions=[{"x": 10, "y": 20, "radius": 1}],
        duration=3000,
    )
    sim = Simulation(sc)
    sim.step(3000)
    assert sim.outcome == CONTAINED, (sim.outcome, sim.outcome_reason)
    sc_ = sim.score()
    assert sc_["contain_bonus"] > 0 and sc_["time_bonus"] > 0 and sc_["total"] > 0


def test_failed_on_structure_limit():
    t = flat(T.GRASS, 40, 40)
    t[20:22, 25:27] = int(T.STRUCTURE)
    sc = Scenario(
        width=40,
        height=40,
        terrain_mode="grid",
        terrain_grid=t,
        wind_speed=6,
        wind_bearing=90,
        ignitions=[{"x": 10, "y": 20, "radius": 1}],
        objectives={"max_structures_lost": 0},
        duration=2000,
    )
    sim = Simulation(sc)
    sim.step(2000)
    assert sim.outcome == FAILED and "structures" in sim.outcome_reason


def test_timeout_outcome():
    t = flat(T.DENSE_FOREST, 40, 40)
    sc = Scenario(
        width=40,
        height=40,
        terrain_mode="grid",
        terrain_grid=t,
        ignitions=[{"x": 20, "y": 20, "radius": 2}],
        duration=60,
    )
    sim = Simulation(sc)
    n = sim.step(500)
    assert sim.outcome == TIMEOUT and n == 60


def test_step_stops_after_outcome():
    t = flat(T.GRASS, 30, 30)
    t[:, 15] = int(T.WATER)
    sc = Scenario(
        width=30, height=30, terrain_mode="grid", terrain_grid=t, ignitions=[{"x": 5, "y": 15}], duration=5000
    )
    sim = Simulation(sc)
    sim.step(5000)
    assert sim.outcome != RUNNING
    assert sim.step(10) == 0


# ---- spotting & slope -------------------------------------------------------------


def test_spotting_jumps_a_break_only_in_high_wind():
    t = flat(T.FOREST, 64, 128)
    t[:, 60:63] = int(T.FIREBREAK)
    crossed = {}
    for wind in (4.0, 18.0):
        g = FireGrid(t, seed=5)
        g.set_wind(wind, 90)
        g.ignite(20, 32, 2)
        for _ in range(500):
            g.step()
        crossed[wind] = bool((g.state[:, 64:] > 0).any())
    assert crossed[4.0] is False
    assert crossed[18.0] is True


def test_no_spotting_below_threshold_wind():
    t = flat(T.FOREST, 48, 48)
    g = FireGrid(t, seed=2)
    g.set_wind(float(FIRE["spot_min_wind"]) - 1, 90)
    g.ignite(10, 24, 2)
    for _ in range(200):
        g.step()
    assert g.spot_fires == 0 and not g.embers


def test_fire_runs_faster_uphill():
    h, w = 64, 128
    t = flat(T.GRASS, h, w)
    ramp = np.tile(np.linspace(0, 200, w, dtype=np.float32), (h, 1))  # rises to the east
    up = FireGrid(t, seed=3, elevation=ramp)
    up.ignite(64, 32, 1)
    down = FireGrid(t, seed=3, elevation=ramp[:, ::-1].copy())
    down.ignite(64, 32, 1)
    for _ in range(80):
        up.step()
        down.step()
    ux = np.nonzero(up.state > 0)[1]
    dx = np.nonzero(down.state > 0)[1]
    assert (ux.max() - 64) > (64 - ux.min())  # spreads further east (uphill)
    assert (dx.max() - 64) < (64 - dx.min())  # mirrored terrain, mirrored result


def test_generated_elevation_is_deterministic():
    a = generate_elevation(32, 32, 9, relief=50.0)
    b = generate_elevation(32, 32, 9, relief=50.0)
    assert np.array_equal(a, b) and a.max() - a.min() > 10


# ---- persistence -----------------------------------------------------------------


def test_savegame_roundtrip_is_exact(tmp_path):
    sim = Simulation(load_scenario(FIXTURES / "prairie_fire.json"))
    uids = {u.utype: u.uid for u in sim.world.units}
    sim.cmd_order(uids["HELICOPTER"], "DROP", points=[(20, 36), (20, 48)])
    sim.cmd_order(uids["CUT_TEAM"], "CUT", points=[(34, 44), (34, 62)])
    sim.step(120)
    p = tmp_path / "s.bbsave"
    sim.save_state(p)
    back = Simulation.load_state(p)
    assert back.state_hash() == sim.state_hash()
    assert len(back.world.units) == len(sim.world.units)
    sim.step(100)
    back.step(100)
    assert back.state_hash() == sim.state_hash()  # RNG state restored too


def test_replay_with_purchases_and_pickup(tmp_path):
    sc = rescue_scenario()
    sc.budget = 5000
    sim = Simulation(sc)
    heli, c1 = sim.world.units[0], sim.world.units[1]
    sim.step(3)
    sim.cmd_spawn("ENGINE")
    sim.cmd_order(heli.uid, PICKUP, unit_id=c1.uid)
    sim.cmd_order(heli.uid, DROPOFF, target=(5, 10), queue=True)
    sim.step(150)
    p = tmp_path / "r.json"
    sim.save_replay(p)
    again = Simulation.replay(p)
    assert again.state_hash() == sim.state_hash()
    assert again.civilian_counts() == sim.civilian_counts()
    assert again.spent == sim.spent


# ---- validation --------------------------------------------------------------------


def test_validation_messages_name_the_field():
    base = json.loads((ROOT / "scenarios" / "prairie_fire.json").read_text())
    bad = dict(base)
    bad["units"] = [{"type": "TANK", "x": 1, "y": 1}]
    with pytest.raises(ScenarioError, match="units\\[0\\].type"):
        validate(bad)
    bad = dict(base)
    bad["ignitions"] = [{"x": 9999, "y": 1}]
    with pytest.raises(ScenarioError, match="outside"):
        validate(bad)
    bad = dict(base)
    bad["terrain"] = {"mode": "grid", "rows": ["GGG", "GG"]}
    with pytest.raises(ScenarioError, match="length"):
        validate(bad)
    bad = dict(base)
    bad["objectives"] = {"win_if_cool": True}
    with pytest.raises(ScenarioError, match="objectives"):
        validate(bad)


def test_all_shipped_scenarios_validate_and_run():
    for f in sorted((ROOT / "scenarios").glob("*.json")):
        s = load_scenario(f)
        sim = Simulation(s)
        sim.step(30)
        assert sim.tick == 30, f


# ---- CLI ---------------------------------------------------------------------------


def test_cli_validate_and_bench(capsys):
    from backburn.__main__ import main

    assert main(["validate", str(ROOT / "scenarios" / "prairie_fire.json")]) == 0
    assert main(["bench", str(ROOT / "scenarios" / "prairie_fire.json"), "--ticks", "20"]) == 0
    out = capsys.readouterr().out
    assert "ticks/s" in out


def test_cli_run_and_replay(tmp_path, capsys):
    from backburn.__main__ import main

    png = tmp_path / "f.png"
    save = tmp_path / "s.bbsave"
    assert (
        main(
            [
                "run",
                str(ROOT / "scenarios" / "prairie_fire.json"),
                "--ticks",
                "40",
                "--png",
                str(png),
                "--save",
                str(save),
            ]
        )
        == 0
    )
    assert png.exists() and save.exists()
    assert main(["resume", str(save), "--ticks", "10"]) == 0
