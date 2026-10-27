"""AppSail / local Flask API + static dashboard."""
from __future__ import print_function

import json
import os
import sys
import traceback

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "core")
ROOT = os.path.dirname(HERE)
_bundled_frontend = os.path.join(HERE, "frontend")
_parent_frontend = os.path.join(ROOT, "frontend")
FRONTEND = _bundled_frontend if os.path.isdir(_bundled_frontend) else _parent_frontend
sys.path.insert(0, CORE)
sys.path.insert(0, ROOT)

from cache import PredCache  # noqa: E402
from forecast import run_forecast  # noqa: E402
from ingest import parse_csv_bytes, rows_to_df  # noqa: E402
from quickml_client import endpoint_configured, predict_features  # noqa: E402
from features import build_features, clean_inputs  # noqa: E402
from store import Store, UsageAdapter  # noqa: E402

app = Flask(__name__, static_folder=FRONTEND, static_url_path="")
origins = os.getenv("CORS_ALLOW_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": origins}})

store = Store()
cache = PredCache(store.conn, store.lock)
usage = UsageAdapter(store)
PRESETS_PATH = os.path.join(FRONTEND, "presets.json")


def _load_presets():
    if os.path.isfile(PRESETS_PATH):
        with open(PRESETS_PATH, "r") as f:
            return json.load(f)
    return {"presets": []}


def _err(status, message, problems=None):
    body = {"error": message, "problems": problems or [message]}
    return jsonify(body), status


@app.route("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    budget = int(os.getenv("DAILY_QUICKML_BUDGET", "300"))
    return jsonify(
        {
            "ok": True,
            "quickml_configured": endpoint_configured(),
            "quickml_calls_today": store.calls_today(),
            "daily_budget": budget,
            "cache": cache.stats(),
            "model_version": os.getenv("QUICKML_MODEL_VERSION", "local-linear-v1"),
        }
    )


@app.route("/api/presets", methods=["GET"])
def presets():
    return jsonify(_load_presets())


@app.route("/api/history", methods=["GET"])
def history():
    limit = int(request.args.get("limit", "20"))
    return jsonify({"runs": store.history(limit=limit), "logs": store.recent_logs(40)})


@app.route("/api/history/<run_id>", methods=["GET"])
def history_one(run_id):
    rec = store.get_run(run_id)
    if rec is None:
        return _err(404, "Unknown run_id")
    return jsonify(rec)


@app.route("/api/forecast", methods=["POST"])
def forecast():
    body = request.get_json(silent=True) or {}
    try:
        rows = body.get("rows")
        if not rows:
            return _err(400, "Request JSON must include a non-empty 'rows' array.")
        df = rows_to_df(rows)
        result = run_forecast(df, body, store, cache, usage, drop_duplicates=False)
        return jsonify(result)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        store.log("ERROR", "API", traceback.format_exc())
        return _err(500, "Forecast failed: %s" % e)


@app.route("/api/predict", methods=["POST"])
def api_predict():
    """Judge-friendly proxy: {data:[...]} -> {predictions:[...], source:...}."""
    body = request.get_json(silent=True) or {}
    data = body.get("data", body if isinstance(body, list) else None)
    if not data:
        return _err(400, "Expected JSON object with 'data' list of rows.")
    budget = int(os.getenv("DAILY_QUICKML_BUDGET", "300"))
    if store.calls_today() >= budget and endpoint_configured():
        return _err(429, "Daily QuickML budget reached (%d)." % budget)
    try:
        df = rows_to_df(data)
        if "b_night" in df.columns:
            from features import FEATURE_COLS
            feats = df[FEATURE_COLS]
        else:
            clean_df, _ = clean_inputs(df, drop_duplicate_keep_last=False)
            feats = build_features(clean_df)
        pred = predict_features(feats, cache=cache, usage=usage, allow_fallback=True)
        return jsonify(
            {
                "predictions": [round(float(v), 4) for v in pred["y"]],
                "source": pred["source"],
                "version": pred["version"],
                "latency_ms": pred["latency_ms"],
            }
        )
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        store.log("ERROR", "PREDICT", traceback.format_exc())
        return _err(502, "Prediction failed: %s" % e)


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return _err(400, "Multipart form must include a 'file' field.")
    f = request.files["file"]
    try:
        df = parse_csv_bytes(f.read(), f.filename or "upload.csv")
        clean_df, quality = clean_inputs(df, drop_duplicate_keep_last=True)
        rows = json.loads(
            clean_df[
                ["timestamp", "ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]
            ].to_json(orient="records")
        )
        store.log(
            "INFO",
            "INGEST",
            "csv rows=%d filled=%s clipped=%s" % (quality["rows"], quality["filled"], quality["clipped"]),
        )
        return jsonify({"rows": rows, "quality": quality})
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(400, "Could not parse CSV: %s" % e)


def create_app():
    return app


if __name__ == "__main__":
    port = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT") or os.environ.get("PORT") or 8080)
    print("Microgrid API on http://127.0.0.1:%s" % port)
    app.run(host="0.0.0.0", port=port, debug=False)
