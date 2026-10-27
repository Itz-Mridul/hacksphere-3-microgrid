"""Assemble forecast: clean -> features -> model -> controller -> battery -> KPIs."""
import math

from battery import BatteryParams, simulate
from controller import evaluate, overload_events, transitions
from features import FEATURE_COLS, T_COOL, build_features
from ingest import clean_for_api
from quickml_client import FALLBACK_COEF, FALLBACK_INTERCEPT, predict_features

SIGMA = 8.52
OVERLOAD_KW = 200.0


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _overload_prob(pred):
    return float(1.0 - _phi((OVERLOAD_KW - pred) / SIGMA))


def _breakdown(feats_row):
    parts = [{"name": "base (midday)", "kw": FALLBACK_INTERCEPT}]
    total = FALLBACK_INTERCEPT
    labels = {
        "b_night": "night 00-05",
        "b_ramp6": "hour 06",
        "b_morning": "morning 07-09",
        "b_evening": "evening 18-22",
        "b_h23": "hour 23",
        "cdd": "cooling degrees",
    }
    for c in FEATURE_COLS:
        w = FALLBACK_COEF[c]
        x = float(feats_row[c])
        contrib = w * x
        if abs(contrib) > 1e-9:
            parts.append({"name": labels[c], "kw": round(contrib, 2), "x": round(x, 3), "coef": w})
            total += contrib
    return {"parts": parts, "sum_kw": round(total, 2)}


def _battery_from_body(body):
    raw = (body or {}).get("battery") or {}
    kwargs = {}
    mapping = {
        "capacity_kwh": "capacity_kwh",
        "soc_start": "soc_start",
        "soc_min": "soc_min",
        "soc_max": "soc_max",
        "p_discharge_max_kw": "p_discharge_max_kw",
        "p_charge_max_kw": "p_charge_max_kw",
        "eff_charge": "eff_charge",
        "eff_discharge": "eff_discharge",
        "pv_kwp": "pv_kwp",
    }
    for k, attr in mapping.items():
        if k in raw and raw[k] is not None:
            kwargs[attr] = raw[k]
    return BatteryParams(**kwargs)


def run_forecast(df, body, store, cache, usage, drop_duplicates=False):
    clean_df, quality = clean_for_api(df, drop_duplicates=drop_duplicates)
    feats = build_features(clean_df)
    pred = predict_features(feats, cache=cache, usage=usage, allow_fallback=True)
    y = pred["y"]
    ctrl = evaluate(clean_df["timestamp"].tolist(), y.tolist())
    solar = clean_df["solar_irradiance_wm2"].astype(float).tolist()
    batt = simulate(ctrl, solar, _battery_from_body(body))
    events = overload_events(batt)
    trans = transitions(batt)
    rows_out = []
    for i, r in enumerate(batt):
        lo = float(r["demand_kw"]) - 1.96 * SIGMA
        hi = float(r["demand_kw"]) + 1.96 * SIGMA
        feat_i = feats.iloc[i]
        rec = dict(r)
        rec["demand_kw"] = round(float(r["demand_kw"]), 2)
        rec["p95_low"] = round(lo, 1)
        rec["p95_high"] = round(hi, 1)
        rec["overload_prob"] = round(_overload_prob(float(r["demand_kw"])), 3)
        rec["ambient_temp_c"] = round(float(clean_df["ambient_temp_c"].iloc[i]), 2)
        rec["humidity_pct"] = round(float(clean_df["humidity_pct"].iloc[i]), 2)
        rec["solar_irradiance_wm2"] = round(float(clean_df["solar_irradiance_wm2"].iloc[i]), 1)
        rec["explain"] = _breakdown(feat_i)
        rows_out.append(rec)

    peak_i = max(range(len(rows_out)), key=lambda j: rows_out[j]["demand_kw"]) if rows_out else 0
    socs = [r["soc_pct"] for r in rows_out]
    min_soc_i = min(range(len(socs)), key=lambda j: socs[j]) if socs else 0
    kpis = {
        "peak_kw": rows_out[peak_i]["demand_kw"] if rows_out else None,
        "peak_at": rows_out[peak_i]["timestamp"] if rows_out else None,
        "renewable_hours": sum(1 for r in rows_out if r["mode"] == "RENEWABLE_STORAGE"),
        "grid_hours": sum(1 for r in rows_out if r["mode"] == "MAIN_GRID"),
        "overload_hours": sum(1 for r in rows_out if r["critical_overload"]),
        "overload_events": len(events),
        "mode_switches": sum(1 for t in trans if t["kind"] == "TRANSITION"),
        "min_soc_pct": socs[min_soc_i] if socs else None,
        "min_soc_at": rows_out[min_soc_i]["timestamp"] if rows_out else None,
    }
    run_id = store.new_run_id()
    result = {
        "run_id": run_id,
        "model": {
            "source": pred["source"],
            "version": pred["version"],
            "latency_ms": pred["latency_ms"],
            "quickml_calls": pred["quickml_calls"],
            "cache_hits": pred["cache_hits"],
            "failed_chunks": pred["failed_chunks"],
        },
        "quality": quality,
        "rows": rows_out,
        "transitions": trans,
        "events": events,
        "kpis": kpis,
    }
    store.save_run(result)
    store.log(
        "INFO",
        "FORECAST",
        "run=%s rows=%d source=%s latency=%dms calls=%d cache_hits=%d"
        % (
            run_id,
            len(rows_out),
            pred["source"],
            pred["latency_ms"],
            pred["quickml_calls"],
            pred["cache_hits"],
        ),
    )
    for w in quality.get("warnings") or []:
        store.log("WARN", "INGEST", w)
    for t in trans:
        store.log("INFO", "CONTROLLER", t["message"])
    return result
