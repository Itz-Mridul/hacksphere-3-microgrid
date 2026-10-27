"""predict.py - HackSphere 3.0 submission script.

Contract: predict(input_df) -> numpy array, one finite value per input row.

Run:
  python predict.py --input sample_test_input.csv --output predictions.csv

Env vars (never hard-code secrets):
  QUICKML_ENDPOINT_URL   published endpoint or AppSail /api/predict proxy
  QUICKML_AUTH_MODE      none | oauth
  QUICKML_ENDPOINT_KEY, CATALYST_ORG, QUICKML_ENV
  ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_ACCOUNTS_URL
  QUICKML_PAYLOAD_MODE   data_list | list | single
  QUICKML_CHUNK, QUICKML_WORKERS, QUICKML_STRICT=1
"""
from __future__ import print_function

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from features import FEATURE_COLS, build_features, clean_inputs

QUICKML_ENDPOINT_URL = os.getenv(
    "QUICKML_ENDPOINT_URL",
    "https://microgrid-api-50047163722.development.catalystappsail.in/api/predict",
)
AUTH_MODE = os.getenv("QUICKML_AUTH_MODE", "none")
PAYLOAD_MODE = os.getenv("QUICKML_PAYLOAD_MODE", "data_list")
CHUNK = int(os.getenv("QUICKML_CHUNK", "100"))
WORKERS = int(os.getenv("QUICKML_WORKERS", "4"))
STRICT = os.getenv("QUICKML_STRICT", "0") == "1"
TIMEOUT_S = 30
RETRIES = 3

_FALLBACK_INTERCEPT = 143.82
_FALLBACK_COEF = {
    "b_night": -53.91,
    "b_ramp6": -11.00,
    "b_morning": 29.90,
    "b_evening": 54.23,
    "b_h23": -33.86,
    "cdd": 8.59,
}

LAST_RUN = {"source": None, "failed_chunks": 0}
_token = {"value": None, "exp": 0.0}


def _fallback(feats):
    y = np.full(len(feats), _FALLBACK_INTERCEPT, dtype=float)
    for c, w in _FALLBACK_COEF.items():
        y += w * feats[c].values
    return y


def _access_token():
    if _token["value"] and time.time() < _token["exp"]:
        return _token["value"]
    base = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
    r = requests.post(
        base + "/oauth/v2/token",
        params={
            "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
            "client_id": os.environ["ZOHO_CLIENT_ID"],
            "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    _token["value"] = r.json()["access_token"]
    _token["exp"] = time.time() + 55 * 60
    return _token["value"]


def _headers():
    h = {"Content-Type": "application/json"}
    if AUTH_MODE == "oauth":
        h.update(
            {
                "Authorization": "Zoho-oauthtoken " + _access_token(),
                "X-QUICKML-ENDPOINT-KEY": os.environ["QUICKML_ENDPOINT_KEY"],
                "CATALYST-ORG": os.environ["CATALYST_ORG"],
                "Environment": os.getenv("QUICKML_ENV", "Production"),
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


def _post(payload):
    last = None
    for k in range(RETRIES):
        try:
            r = requests.post(
                QUICKML_ENDPOINT_URL,
                json=payload,
                headers=_headers(),
                timeout=TIMEOUT_S,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 ** k)
    raise last


def _call_chunk(records):
    if PAYLOAD_MODE == "single":
        return [_extract(_post(rec), 1)[0] for rec in records]
    payload = {"data": records} if PAYLOAD_MODE == "data_list" else records
    return _extract(_post(payload), len(records))


def predict(input_df):
    """Returns np.ndarray of predicted demand (kW), one value per input row."""
    df, _ = clean_inputs(input_df.reset_index(drop=True), drop_duplicate_keep_last=False)
    feats = build_features(df)
    n = len(feats)
    placeholder = (
        QUICKML_ENDPOINT_URL == "YOUR_PUBLISHED_QUICKML_ENDPOINT_URL_HERE"
        or not QUICKML_ENDPOINT_URL
    )
    if placeholder:
        if STRICT:
            raise RuntimeError("QUICKML_ENDPOINT_URL not set")
        print(
            "WARNING: no endpoint configured -> LOCAL FALLBACK model (NOT QuickML).",
            file=sys.stderr,
        )
        LAST_RUN.update(source="fallback", failed_chunks=1)
        return _fallback(feats)

    records = feats[FEATURE_COLS].to_dict(orient="records")
    chunks = [records[i:i + CHUNK] for i in range(0, n, CHUNK)]
    out = [None] * len(chunks)
    failed = 0

    def work(i):
        return i, _call_chunk(chunks[i])

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        future_to_i = dict((ex.submit(work, i), i) for i in range(len(chunks)))
        for fut, i in future_to_i.items():
            try:
                idx, vals = fut.result()
                out[idx] = np.array(vals, dtype=float)
            except Exception as e:
                if STRICT:
                    raise
                failed += 1
                lo = i * CHUNK
                print(
                    "WARNING: chunk %d failed (%s) -> LOCAL FALLBACK for rows %d-%d"
                    % (i, e, lo, lo + len(chunks[i]) - 1),
                    file=sys.stderr,
                )
                out[i] = _fallback(feats.iloc[lo:lo + len(chunks[i])])

    if failed == 0:
        source = "quickml"
    elif failed == len(chunks):
        source = "fallback"
    else:
        source = "mixed"
    LAST_RUN.update(source=source, failed_chunks=failed)
    res = np.concatenate(out).astype(float)
    if not np.isfinite(res).all():
        raise ValueError("non-finite predictions")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="sample_test_input.csv")
    ap.add_argument("--output", default="predictions.csv")
    a = ap.parse_args()
    src = pd.read_csv(a.input)
    preds = predict(src)
    pd.DataFrame(
        {"timestamp": src["timestamp"], "target_demand_kw": np.round(preds, 2)}
    ).to_csv(a.output, index=False)
    print(
        "Wrote %d predictions to %s | source=%s failed_chunks=%d"
        % (len(preds), a.output, LAST_RUN["source"], LAST_RUN["failed_chunks"])
    )
