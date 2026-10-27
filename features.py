"""features.py - ONE feature definition shared by training CSV prep, predict.py, and AppSail.

Row-wise only (no lag / rolling), so it is safe even if test rows are shuffled.
Python 3.9 compatible.
"""
import numpy as np
import pandas as pd

T_COOL = 24.0
FEATURE_COLS = ["b_night", "b_ramp6", "b_morning", "b_evening", "b_h23", "cdd"]
RAW_COLS = ["timestamp", "ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]
SAFE_FILL = {
    "ambient_temp_c": 18.5,
    "humidity_pct": 70.0,
    "solar_irradiance_wm2": 0.0,
}
LIMITS = {
    "ambient_temp_c": (-30.0, 45.0),
    "humidity_pct": (0.0, 100.0),
    "solar_irradiance_wm2": (0.0, 1400.0),
}


def normalize_columns(df):
    """Trim header spaces and ignore case. Do not guess other spellings."""
    expected = dict((c.lower(), c) for c in RAW_COLS)
    extra = ["target_demand_kw"]
    for name in extra:
        expected[name] = name
    mapping = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in expected:
            mapping[col] = expected[key]
    return df.rename(columns=mapping)


def hour_from_timestamp(ts):
    """Parse YYYY-MM-DD HH:MM:SS (also ISO T). Naive time, no timezone shift."""
    return pd.to_datetime(ts, errors="coerce").dt.hour


def build_features(df):
    h = hour_from_timestamp(df["timestamp"])
    t = pd.to_numeric(df["ambient_temp_c"], errors="coerce")
    out = pd.DataFrame(index=df.index)
    out["b_night"] = ((h >= 0) & (h <= 5)).astype(float)
    out["b_ramp6"] = (h == 6).astype(float)
    out["b_morning"] = ((h >= 7) & (h <= 9)).astype(float)
    out["b_evening"] = ((h >= 18) & (h <= 22)).astype(float)
    out["b_h23"] = (h == 23).astype(float)
    out["cdd"] = np.maximum(0.0, t - T_COOL)
    unknown = h.isna()
    if unknown.any():
        for col in ["b_night", "b_ramp6", "b_morning", "b_evening", "b_h23"]:
            out.loc[unknown, col] = 0.0
        out.loc[unknown, "cdd"] = np.maximum(0.0, t[unknown] - T_COOL)
    return out[FEATURE_COLS]


def clean_inputs(df, drop_duplicate_keep_last=False, max_rows=1000):
    """Validate and repair raw rows. Never drops rows unless UI duplicate mode.

    Returns (clean_df, report). One prediction per input row is required for predict.py.
    """
    d = normalize_columns(df)
    miss = [c for c in RAW_COLS if c not in d.columns]
    if miss:
        raise ValueError("Missing columns: %s. Expected: %s" % (miss, RAW_COLS))
    d = d.copy()
    warnings = []
    filled = {}
    clipped = {}

    ts_raw = d["timestamp"].astype(str).str.strip()
    ts_iso = ts_raw.str.replace("T", " ", n=1, regex=False)
    ts = pd.to_datetime(ts_iso, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    still_bad = ts.isna()
    if still_bad.any():
        ts.loc[still_bad] = pd.to_datetime(ts_iso[still_bad], errors="coerce")
    bad_timestamp = int(ts.isna().sum())
    if bad_timestamp:
        warnings.append("%d timestamp(s) could not be parsed; hour treated as unknown (base level)." % bad_timestamp)
    d["timestamp"] = ts_iso
    d["_ts"] = ts

    if drop_duplicate_keep_last and ts.notna().any():
        before = len(d)
        d = d.loc[~d["_ts"].duplicated(keep="last")].copy()
        dropped = before - len(d)
        if dropped:
            warnings.append("Dropped %d duplicate timestamp row(s); kept last." % dropped)

    for c, (lo, hi) in LIMITS.items():
        d[c] = pd.to_numeric(d[c], errors="coerce")
        n_missing = int(d[c].isna().sum())
        if n_missing:
            ordered = d.sort_values("_ts", kind="mergesort")
            ordered[c] = ordered[c].interpolate(method="linear", limit=3, limit_direction="both")
            ordered[c] = ordered[c].fillna(SAFE_FILL[c])
            d.loc[ordered.index, c] = ordered[c]
            filled[c] = n_missing
            warnings.append("Filled %d missing/invalid value(s) in %s." % (n_missing, c))
        n_clip = int(((d[c] < lo) | (d[c] > hi)).sum())
        if n_clip:
            d[c] = d[c].clip(lo, hi)
            clipped[c] = n_clip
            warnings.append("Clipped %d out-of-range value(s) in %s to [%s, %s]." % (n_clip, c, lo, hi))

    n = len(d)
    if n < 24:
        warnings.append("Fewer than 24 rows (%d). Forecast still runs." % n)
    if max_rows is not None and n > max_rows:
        raise ValueError("Too many rows: %d (max %d)." % (n, max_rows))

    gaps = 0
    valid_ts = d["_ts"].dropna().sort_values()
    if len(valid_ts) >= 2:
        deltas = valid_ts.diff().dropna()
        gaps = int((deltas != pd.Timedelta(hours=1)).sum())
        if gaps:
            warnings.append("Found %d non-hourly gap(s). Row-wise features still apply." % gaps)

    d = d.drop(columns=["_ts"])
    report = {
        "rows": int(n),
        "bad_timestamp": bad_timestamp,
        "filled": filled,
        "clipped": clipped,
        "warnings": warnings,
        "gaps": gaps,
    }
    return d, report
