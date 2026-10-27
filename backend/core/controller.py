"""controller.py - PDF section 6 rules. Mode never depends on battery state."""
import pandas as pd

RENEWABLE = "RENEWABLE_STORAGE"
MAIN = "MAIN_GRID"
DEMAND_LIMIT_KW = 160.0
OVERLOAD_KW = 200.0
PEAK_START_H, PEAK_END_H = 18, 22


def decide(demand_kw, hour):
    demand_kw = float(demand_kw)
    try:
        hour = int(hour)
        hour_ok = 0 <= hour <= 23
    except (TypeError, ValueError):
        hour = -1
        hour_ok = False
    in_peak = hour_ok and PEAK_START_H <= hour <= PEAK_END_H
    high_demand = demand_kw > DEMAND_LIMIT_KW
    if high_demand or in_peak:
        mode = RENEWABLE
    else:
        mode = MAIN
    parts = []
    if high_demand:
        parts.append("demand>160")
    if in_peak:
        parts.append("peak-window 18-22")
    reason = " + ".join(parts) if parts else "demand<=160 & off-peak"
    return {
        "mode": mode,
        "critical_overload": bool(demand_kw >= OVERLOAD_KW),
        "reason": reason,
    }


def evaluate(timestamps, demands):
    ts_series = pd.Series(list(timestamps)).astype(str)
    hours = pd.to_datetime(ts_series.str.replace("T", " ", n=1, regex=False), errors="coerce").dt.hour
    rows = []
    for t, h, d in zip(timestamps, hours, demands):
        hour_val = int(h) if pd.notna(h) else -1
        rec = decide(float(d), hour_val)
        rec["timestamp"] = str(t)
        rec["hour"] = hour_val
        rec["demand_kw"] = float(d)
        rows.append(rec)
    return rows


def transitions(rows):
    events = []
    if not rows:
        return events
    first = rows[0]
    events.append({
        "at": first["timestamp"],
        "from": None,
        "to": first["mode"],
        "kind": "INITIAL_STATE",
        "message": "INITIAL STATE %s at %s" % (first["mode"], first["timestamp"]),
    })
    for prev, cur in zip(rows, rows[1:]):
        if prev["mode"] != cur["mode"]:
            events.append({
                "at": cur["timestamp"],
                "from": prev["mode"],
                "to": cur["mode"],
                "kind": "TRANSITION",
                "message": "TRANSITION %s -> %s at %s" % (prev["mode"], cur["mode"], cur["timestamp"]),
            })
    return events


def overload_events(rows):
    """Group consecutive overload hours into events."""
    ev = []
    cur = None
    for r in rows:
        if r["critical_overload"]:
            if cur is None:
                cur = {
                    "start": r["timestamp"],
                    "end": r["timestamp"],
                    "hours": 1,
                    "peak_kw": r["demand_kw"],
                }
            else:
                cur["end"] = r["timestamp"]
                cur["hours"] += 1
                cur["peak_kw"] = max(cur["peak_kw"], r["demand_kw"])
        elif cur is not None:
            ev.append(cur)
            cur = None
    if cur is not None:
        ev.append(cur)
    return ev
