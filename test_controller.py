"""Run: python test_controller.py   or   python -m pytest -q test_controller.py"""
import random

import pandas as pd

from battery import BatteryParams, simulate
from controller import MAIN, RENEWABLE, decide, evaluate, overload_events

BOUNDARY = [
    (160.00, 10, MAIN, False),
    (160.01, 10, RENEWABLE, False),
    (100, 17, MAIN, False),
    (100, 18, RENEWABLE, False),
    (100, 22, RENEWABLE, False),
    (100, 23, MAIN, False),
    (150, 12, MAIN, False),
    (199.99, 3, RENEWABLE, False),
    (200.0, 3, RENEWABLE, True),
    (250, 3, RENEWABLE, True),
    (205, 19, RENEWABLE, True),
]


def test_boundaries():
    for d, h, mode, ov in BOUNDARY:
        r = decide(d, h)
        assert (r["mode"], r["critical_overload"]) == (mode, ov), (d, h, r)


def test_random_matches_plain_formula():
    rnd = random.Random(7)
    for _ in range(10000):
        d, h = rnd.uniform(0, 400), rnd.randint(0, 23)
        r = decide(d, h)
        assert r["mode"] == (RENEWABLE if (d > 160 or 18 <= h <= 22) else MAIN)
        assert r["critical_overload"] == (d >= 200)


def test_events_grouping():
    ts = pd.date_range("2015-06-16 15:00", periods=6, freq="h").astype(str)
    rows = evaluate(list(ts), [210, 220, 150, 205, 100, 230])
    ev = overload_events(rows)
    assert [(e["hours"], round(e["peak_kw"])) for e in ev] == [(2, 220), (1, 205), (1, 230)]


def test_battery_invariants():
    rnd = random.Random(3)
    p = BatteryParams()
    ts = pd.date_range("2015-06-16 00:00", periods=24 * 14, freq="h").astype(str)
    dem = [rnd.uniform(60, 300) for _ in ts]
    sol = [rnd.uniform(0, 490) if 6 <= int(t[11:13]) <= 18 else 0.0 for t in ts]
    out = simulate(evaluate(list(ts), dem), sol, p)
    prev = p.soc_start * 100
    for r in out:
        assert p.soc_min * 100 - 0.11 <= r["soc_pct"] <= p.soc_max * 100 + 0.11, r
        if r["mode"] == MAIN:
            assert r["battery_to_load_kw"] == 0.0 and r["shortfall_kw"] == 0.0
        else:
            assert r["grid_charge_kw"] == 0.0
        exp = (r["grid_charge_kw"] + r["pv_charge_kw"]) * p.eff_charge - r["battery_to_load_kw"] / p.eff_discharge
        assert abs((r["soc_pct"] - prev) / 100 * p.capacity_kwh - exp) < 3.0, (r, prev)
        prev = r["soc_pct"]


if __name__ == "__main__":
    for f in (
        test_boundaries,
        test_random_matches_plain_formula,
        test_events_grouping,
        test_battery_invariants,
    ):
        f()
        print("PASS", f.__name__)
