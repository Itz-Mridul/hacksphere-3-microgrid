"""QuickML HTTP client with OAuth token cache, retries, and local linear fallback."""
from __future__ import print_function

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

from features import FEATURE_COLS

FALLBACK_INTERCEPT = 143.82
FALLBACK_COEF = {
    "b_night": -53.91,
    "b_ramp6": -11.00,
    "b_morning": 29.90,
    "b_evening": 54.23,
    "b_h23": -33.86,
    "cdd": 8.59,
}

TIMEOUT_S = 30
RETRIES = 3
_token = {"value": None, "exp": 0.0}


def fallback_predict(feats_df):
    y = np.full(len(feats_df), FALLBACK_INTERCEPT, dtype=float)
    for c, w in FALLBACK_COEF.items():
        y += w * feats_df[c].values
    return y


def feature_key(record, model_version):
    payload = []
    for c in FEATURE_COLS:
        v = float(record[c])
        if c == "cdd":
            v = round(v, 3)
        payload.append(v)
    raw = json.dumps([payload, model_version], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _access_token():
    if _token["value"] and time.time() < _token["exp"]:
        return _token["value"]
    refresh = os.getenv("ZOHO_REFRESH_TOKEN")
    client_id = os.getenv("ZOHO_CLIENT_ID")
    secret = os.getenv("ZOHO_CLIENT_SECRET")
    if not (refresh and client_id and secret):
        raise RuntimeError("oauth credentials missing")
    base = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
    r = requests.post(
        base + "/oauth/v2/token",
        params={
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    _token["value"] = r.json()["access_token"]
    _token["exp"] = time.time() + 55 * 60
    return _token["value"]


def _headers(auth_mode):
    h = {"Content-Type": "application/json"}
    if auth_mode == "oauth":
        h.update(
            {
                "Authorization": "Zoho-oauthtoken " + _access_token(),
                "X-QUICKML-ENDPOINT-KEY": os.environ["QUICKML_ENDPOINT_KEY"],
                "CATALYST-ORG": os.getenv("CATALYST_ORG") or os.getenv("X_ZOHO_CATALYST_ORG", ""),
                "Environment": os.getenv("QUICKML_ENV", "Development"),
            }
        )
    return h


def _to_float(v):
    if isinstance(v, dict):
        v = next((x for x in v.values() if isinstance(x, (int, float, str))), None)
    return float(v)


def _extract(js, n):
    if isinstance(js, dict):
        js = js.get("predictions", js.get("result", js.get("data")))
    if not isinstance(js, (list, tuple)):
        js = [js]
    vals = [_to_float(v) for v in js]
    if len(vals) != n:
        raise ValueError("endpoint returned %d values for %d rows" % (len(vals), n))
    return vals


def _post(url, payload, headers):
    last = None
    for k in range(RETRIES):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_S)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 ** k)
    raise last


def _call_chunk(url, records, payload_mode, headers):
    if payload_mode == "single":
        return [_extract(_post(url, rec, headers), 1)[0] for rec in records]
    payload = {"data": records} if payload_mode == "data_list" else records
    return _extract(_post(url, payload, headers), len(records))


def endpoint_configured():
    url = os.getenv("QUICKML_ENDPOINT_URL", "YOUR_PUBLISHED_QUICKML_ENDPOINT_URL_HERE")
    return bool(url) and url != "YOUR_PUBLISHED_QUICKML_ENDPOINT_URL_HERE"


def predict_features(feats_df, cache=None, usage=None, allow_fallback=True):
    """Predict from a feature frame. Returns dict with y, source, latency_ms, calls, failed_chunks."""
    t0 = time.time()
    n = len(feats_df)
    records = feats_df[FEATURE_COLS].to_dict(orient="records")
    model_version = os.getenv("QUICKML_MODEL_VERSION", "local-linear-v1")
    y = np.zeros(n, dtype=float)
    from_cache = np.zeros(n, dtype=bool)
    need_idx = []
    need_recs = []

    if cache is not None:
        for i, rec in enumerate(records):
            hit = cache.get(feature_key(rec, model_version))
            if hit is not None:
                y[i] = hit
                from_cache[i] = True
            else:
                need_idx.append(i)
                need_recs.append(rec)
    else:
        need_idx = list(range(n))
        need_recs = records

    url = os.getenv("QUICKML_ENDPOINT_URL", "YOUR_PUBLISHED_QUICKML_ENDPOINT_URL_HERE")
    auth_mode = os.getenv("QUICKML_AUTH_MODE", "none")
    payload_mode = os.getenv("QUICKML_PAYLOAD_MODE", "data_list")
    chunk = int(os.getenv("QUICKML_CHUNK", "100"))
    workers = int(os.getenv("QUICKML_WORKERS", "4"))
    strict = os.getenv("QUICKML_STRICT", "0") == "1"
    calls = 0
    failed_chunks = 0
    remote_ok = 0

    if need_recs and endpoint_configured():
        chunks = []
        chunk_idx = []
        for i in range(0, len(need_recs), chunk):
            chunks.append(need_recs[i:i + chunk])
            chunk_idx.append(need_idx[i:i + chunk])
        headers = _headers(auth_mode)

        def work(ci):
            return ci, _call_chunk(url, chunks[ci], payload_mode, headers)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = dict((ex.submit(work, ci), ci) for ci in range(len(chunks)))
            for fut, ci in futs.items():
                try:
                    _, vals = fut.result()
                    calls += 1
                    remote_ok += len(vals)
                    for local_i, pred in zip(chunk_idx[ci], vals):
                        y[local_i] = pred
                        if cache is not None:
                            cache.put(feature_key(records[local_i], model_version), float(pred), model_version)
                except Exception as e:
                    failed_chunks += 1
                    if strict or not allow_fallback:
                        raise
                    print("WARNING: QuickML chunk failed (%s) -> fallback" % e, file=sys.stderr)
                    idx = chunk_idx[ci]
                    sub = feats_df.iloc[idx]
                    fb = fallback_predict(sub)
                    for local_i, pred in zip(idx, fb):
                        y[local_i] = pred
    elif need_recs:
        if strict:
            raise RuntimeError("QUICKML_ENDPOINT_URL not set")
        fb = fallback_predict(feats_df.iloc[need_idx])
        for local_i, pred in zip(need_idx, fb):
            y[local_i] = pred
            if cache is not None:
                cache.put(feature_key(records[local_i], model_version), float(pred), model_version)

    if usage is not None and calls:
        usage.add(calls)

    cache_hits = int(from_cache.sum())
    if cache_hits == n:
        source = "cache"
    elif not endpoint_configured() or (need_recs and remote_ok == 0 and failed_chunks == (len(need_recs) + chunk - 1) // chunk):
        source = "fallback" if cache_hits == 0 else "mixed"
    elif failed_chunks:
        source = "mixed"
    elif cache_hits:
        source = "mixed" if need_recs else "cache"
        if remote_ok and cache_hits:
            source = "mixed"
        elif remote_ok:
            source = "quickml"
    else:
        source = "quickml" if endpoint_configured() else "fallback"
    if endpoint_configured() and need_recs and remote_ok == len(need_recs) and failed_chunks == 0:
        source = "quickml" if cache_hits == 0 else "mixed"

    latency_ms = int((time.time() - t0) * 1000)
    return {
        "y": y,
        "source": source,
        "version": model_version,
        "latency_ms": latency_ms,
        "quickml_calls": calls,
        "failed_chunks": failed_chunks,
        "cache_hits": cache_hits,
        "n": n,
    }
