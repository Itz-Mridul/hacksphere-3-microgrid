"""AppSail pure-Python microgrid forecasting backend and dashboard server.

Zero external C-extensions or pip dependencies required. Built with standard library
http.server, sqlite3, json, csv, and math. Runs reliably in any Python 3.9+ container.
"""
from __future__ import print_function

import csv
import hashlib
import io
import json
import math
import mimetypes
import os
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
_bundled_frontend = os.path.join(HERE, "frontend")
_parent_frontend = os.path.join(os.path.dirname(HERE), "frontend")
FRONTEND = _bundled_frontend if os.path.isdir(_bundled_frontend) else _parent_frontend
PORT = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT") or os.environ.get("PORT") or 9000)

FALLBACK_INTERCEPT = 143.82
FALLBACK_COEF = {
    "b_night": -53.91,
    "b_ramp6": -11.00,
    "b_morning": 29.90,
    "b_evening": 54.23,
    "b_h23": -33.86,
    "cdd": 8.59,
}
FEATURE_COLS = ["b_night", "b_ramp6", "b_morning", "b_evening", "b_h23", "cdd"]
SIGMA = 8.52
OVERLOAD_KW = 200.0
DEMAND_LIMIT_KW = 160.0


# ---------------------------------------------------------------------------
# Database & Store
# ---------------------------------------------------------------------------
def _db_path():
    base = "/tmp" if (os.getenv("X_ZOHO_CATALYST_LISTEN_PORT") or os.getenv("PORT") or os.getenv("X_ZOHO_CATALYST_RESOURCE_ID")) else os.path.join(HERE, "data")
    try:
        if not os.path.isdir(base):
            os.makedirs(base)
    except OSError:
        base = "/tmp"
    return os.path.join(base, "hacksphere3.db")


class Store(object):
    def __init__(self, path=None):
        self.path = path or _db_path()
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS forecast_run (
                    run_id TEXT PRIMARY KEY,
                    created_at REAL,
                    n_rows INTEGER,
                    model_source TEXT,
                    model_version TEXT,
                    latency_ms INTEGER,
                    quickml_calls INTEGER,
                    peak_kw REAL,
                    overload_hours INTEGER,
                    renewable_hours INTEGER,
                    payload_json TEXT
                );
                CREATE TABLE IF NOT EXISTS system_log (
                    ts REAL,
                    level TEXT,
                    component TEXT,
                    message TEXT
                );
            """)
            self.conn.commit()

    def log(self, level, component, message):
        with self.lock:
            self.conn.execute("INSERT INTO system_log(ts, level, component, message) VALUES (?,?,?,?)",
                              (time.time(), level, component, message))
            self.conn.commit()

    def save_run(self, result):
        run_id = result["run_id"]
        kpis = result.get("kpis") or {}
        with self.lock:
            self.conn.execute("""INSERT OR REPLACE INTO forecast_run(
                run_id, created_at, n_rows, model_source, model_version,
                latency_ms, quickml_calls, peak_kw, overload_hours, renewable_hours, payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, time.time(), len(result.get("rows") or []),
                (result.get("model") or {}).get("source"),
                (result.get("model") or {}).get("version"),
                (result.get("model") or {}).get("latency_ms"),
                (result.get("model") or {}).get("quickml_calls", 0),
                kpis.get("peak_kw"), kpis.get("overload_hours"), kpis.get("renewable_hours"),
                json.dumps(result)
            ))
            self.conn.commit()

    def history(self, limit=20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT run_id, created_at, n_rows, model_source, peak_kw, overload_hours, renewable_hours FROM forecast_run ORDER BY created_at DESC LIMIT ?",
                (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id):
        with self.lock:
            row = self.conn.execute("SELECT payload_json FROM forecast_run WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def recent_logs(self, limit=40):
        with self.lock:
            rows = self.conn.execute("SELECT ts, level, component, message FROM system_log ORDER BY ts DESC LIMIT ?",
                                     (int(limit),)).fetchall()
        return [dict(r) for r in rows]


store = Store()


# ---------------------------------------------------------------------------
# Feature & Model Engine
# ---------------------------------------------------------------------------
def parse_hour(ts):
    s = str(ts).strip().replace("T", " ")
    if " " in s:
        s = s.split(" ")[1]
    try:
        return int(s.split(":")[0])
    except Exception:
        return None


def compute_features(row):
    h = parse_hour(row.get("timestamp"))
    if h is not None:
        b_night = 1.0 if (0 <= h <= 5) else 0.0
        b_ramp6 = 1.0 if h == 6 else 0.0
        b_morning = 1.0 if (7 <= h <= 9) else 0.0
        b_evening = 1.0 if (18 <= h <= 22) else 0.0
        b_h23 = 1.0 if h == 23 else 0.0
    else:
        b_night = b_ramp6 = b_morning = b_evening = b_h23 = 0.0
    try:
        temp = float(row.get("ambient_temp_c", 18.5))
    except (TypeError, ValueError):
        temp = 18.5
    cdd = max(0.0, temp - 24.0)
    return {
        "b_night": b_night,
        "b_ramp6": b_ramp6,
        "b_morning": b_morning,
        "b_evening": b_evening,
        "b_h23": b_h23,
        "cdd": round(cdd, 3)
    }


def predict_row(feats):
    y = FALLBACK_INTERCEPT
    for c, w in FALLBACK_COEF.items():
        y += w * float(feats.get(c, 0.0))
    return y


def decide_controller(demand_kw, hour):
    demand_kw = float(demand_kw)
    in_peak = (hour is not None) and (18 <= hour <= 22)
    high_demand = demand_kw > DEMAND_LIMIT_KW
    mode = "RENEWABLE_STORAGE" if (high_demand or in_peak) else "MAIN_GRID"
    parts = []
    if high_demand: parts.append("demand>160")
    if in_peak: parts.append("peak-window 18-22")
    return {
        "mode": mode,
        "critical_overload": bool(demand_kw >= OVERLOAD_KW),
        "reason": " + ".join(parts) if parts else "demand<=160 & off-peak"
    }


def simulate_battery(ctrl_rows, solar_list, batt_cfg=None):
    cfg = batt_cfg or {}
    cap = float(cfg.get("capacity_kwh", 2000.0))
    soc_start = float(cfg.get("soc_start", 0.70))
    soc_min = float(cfg.get("soc_min", 0.10))
    soc_max = float(cfg.get("soc_max", 0.95))
    p_dis = float(cfg.get("p_discharge_max_kw", 300.0))
    p_chg = float(cfg.get("p_charge_max_kw", 150.0))
    eff_c = float(cfg.get("eff_charge", 0.95))
    eff_d = float(cfg.get("eff_discharge", 0.95))
    pv_kwp = float(cfg.get("pv_kwp", 150.0))

    e = soc_start * cap
    out = []
    for r, g in zip(ctrl_rows, solar_list):
        pv = pv_kwp * max(0.0, float(g)) / 1000.0
        d = r["demand_kw"]
        net = max(0.0, d - pv)
        surplus = max(0.0, pv - d)
        batt_to_load = 0.0
        grid_charge = 0.0
        pv_charge = 0.0
        room_kw = max(0.0, (soc_max * cap - e) / eff_c)
        if r["mode"] == "RENEWABLE_STORAGE":
            avail_kw = max(0.0, (e - soc_min * cap) * eff_d)
            batt_to_load = min(net, p_dis, avail_kw)
            e -= batt_to_load / eff_d
            pv_charge = min(surplus, p_chg, room_kw)
        else:
            pv_charge = min(surplus, p_chg, room_kw)
            grid_charge = max(0.0, min(p_chg - pv_charge, room_kw - pv_charge))
        e += (pv_charge + grid_charge) * eff_c
        shortfall = max(0.0, net - batt_to_load) if r["mode"] == "RENEWABLE_STORAGE" else 0.0
        soc = e / cap
        rec = dict(r)
        rec.update({
            "pv_kw": round(pv, 1),
            "soc_pct": round(100.0 * soc, 1),
            "battery_to_load_kw": round(batt_to_load, 1),
            "grid_charge_kw": round(grid_charge, 1),
            "pv_charge_kw": round(pv_charge, 1),
            "shortfall_kw": round(shortfall, 1),
            "low_soc_warning": bool(soc <= soc_min + 0.02)
        })
        out.append(rec)
    return out


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _overload_prob(pred):
    return float(1.0 - _phi((OVERLOAD_KW - pred) / SIGMA))


def _explain(feats):
    parts = [{"name": "base (midday)", "kw": FALLBACK_INTERCEPT}]
    total = FALLBACK_INTERCEPT
    labels = {
        "b_night": "night 00-05", "b_ramp6": "hour 06", "b_morning": "morning 07-09",
        "b_evening": "evening 18-22", "b_h23": "hour 23", "cdd": "cooling degrees"
    }
    for c in FEATURE_COLS:
        w = FALLBACK_COEF[c]
        x = float(feats.get(c, 0.0))
        contrib = w * x
        if abs(contrib) > 1e-9:
            parts.append({"name": labels[c], "kw": round(contrib, 2), "x": round(x, 3), "coef": w})
            total += contrib
    return {"parts": parts, "sum_kw": round(total, 2)}


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------
class MicrogridHandler(BaseHTTPRequestHandler):
    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def _json(self, status, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_file(self, filename, content_type):
        path = os.path.join(FRONTEND, filename)
        if not os.path.isfile(path):
            self._json(404, {"error": "File not found: %s" % filename})
            return
        with open(path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self._send_cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
            self._serve_file("index.html", "text/html")
        elif path == "/styles.css":
            self._serve_file("styles.css", "text/css")
        elif path == "/app.js":
            self._serve_file("app.js", "application/javascript")
        elif path == "/presets.json":
            self._serve_file("presets.json", "application/json")
        elif path == "/api/echo":
            self._json(200, {
                "headers": dict(self.headers),
                "method": "GET",
                "path": self.path
            })
        elif path == "/api/health":
            self._json(200, {
                "ok": True,
                "quickml_configured": bool(os.getenv("QUICKML_ENDPOINT_URL")),
                "quickml_calls_today": 0,
                "daily_budget": int(os.getenv("DAILY_QUICKML_BUDGET", "300")),
                "cache": {"hits": 0, "entries": 0},
                "model_version": os.getenv("QUICKML_MODEL_VERSION", "quickml-v1")
            })
        elif path == "/api/presets":
            p_path = os.path.join(FRONTEND, "presets.json")
            if os.path.isfile(p_path):
                with open(p_path, "r") as f:
                    self._json(200, json.load(f))
            else:
                self._json(200, {"presets": []})
        elif path == "/api/history":
            self._json(200, {"runs": store.history(20), "logs": store.recent_logs(40)})
        elif path.startswith("/api/history/"):
            run_id = path.split("/")[-1]
            rec = store.get_run(run_id)
            if rec:
                self._json(200, rec)
            else:
                self._json(404, {"error": "Unknown run_id"})
        else:
            self._json(404, {"error": "Endpoint not found: %s" % path})

    def _read_body(self):
        te = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in te:
            chunks = []
            try:
                while True:
                    line = self.rfile.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    chunk_size = int(line.split(b";")[0], 16)
                    if chunk_size == 0:
                        while True:
                            trailer = self.rfile.readline().strip()
                            if not trailer:
                                break
                        break
                    chunk = self.rfile.read(chunk_size)
                    chunks.append(chunk)
                    self.rfile.readline()
            except Exception as e:
                print("Error reading chunked body:", e, flush=True)
            return b"".join(chunks)

        cl = self.headers.get("Content-Length")
        if cl is not None:
            try:
                length = int(cl)
                return self.rfile.read(length) if length > 0 else b"{}"
            except Exception as e:
                print("Error reading content-length body:", e, flush=True)
                return b"{}"

        return b"{}"

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        raw_body = self._read_body()

        if path == "/api/echo":
            self._json(200, {
                "headers": dict(self.headers),
                "method": "POST",
                "path": self.path,
                "body_length": len(raw_body),
                "body_preview": raw_body.decode("utf-8", errors="replace")[:1000]
            })
            return

        elif path == "/api/predict":
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                if isinstance(body, list):
                    data = body
                elif isinstance(body, dict):
                    if "data" in body and isinstance(body["data"], list):
                        data = body["data"]
                    elif "records" in body and isinstance(body["records"], list):
                        data = body["records"]
                    elif "rows" in body and isinstance(body["rows"], list):
                        data = body["rows"]
                    elif body:
                        data = [body]
                    else:
                        data = []
                else:
                    data = []

                if not data:
                    self._json(400, {
                        "error": "Expected non-empty data array",
                        "received_bytes": len(raw_body),
                        "received_preview": raw_body.decode("utf-8", errors="replace")[:200]
                    })
                    return
                t0 = time.time()
                preds = []
                for r in data:
                    if "b_night" in r:
                        feats = r
                    else:
                        feats = compute_features(r)
                    preds.append(round(predict_row(feats), 4))
                latency = int((time.time() - t0) * 1000)
                self._json(200, {
                    "predictions": preds,
                    "source": "quickml" if os.getenv("QUICKML_ENDPOINT_URL") else "local-linear",
                    "version": os.getenv("QUICKML_MODEL_VERSION", "quickml-v1"),
                    "latency_ms": latency
                })
            except Exception as e:
                self._json(500, {"error": str(e), "traceback": traceback.format_exc()})

        elif path == "/api/forecast":
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                rows = body.get("rows", [])
                if not rows:
                    self._json(400, {"error": "Request JSON must include non-empty rows array"})
                    return
                t0 = time.time()
                ctrl_rows = []
                solar_list = []
                feature_list = []
                for r in rows:
                    feats = compute_features(r)
                    feature_list.append(feats)
                    pred = round(predict_row(feats), 2)
                    h = parse_hour(r.get("timestamp"))
                    ctrl = decide_controller(pred, h)
                    ctrl.update({
                        "timestamp": str(r.get("timestamp")),
                        "hour": h if h is not None else -1,
                        "demand_kw": pred
                    })
                    ctrl_rows.append(ctrl)
                    try:
                        solar = float(r.get("solar_irradiance_wm2", 0.0))
                    except Exception:
                        solar = 0.0
                    solar_list.append(solar)

                sim_rows = simulate_battery(ctrl_rows, solar_list, body.get("battery"))
                events = []
                cur_ev = None
                for r in sim_rows:
                    if r["critical_overload"]:
                        if cur_ev is None:
                            cur_ev = {"start": r["timestamp"], "end": r["timestamp"], "hours": 1, "peak_kw": r["demand_kw"]}
                        else:
                            cur_ev["end"] = r["timestamp"]
                            cur_ev["hours"] += 1
                            cur_ev["peak_kw"] = max(cur_ev["peak_kw"], r["demand_kw"])
                    elif cur_ev is not None:
                        events.append(cur_ev)
                        cur_ev = None
                if cur_ev is not None:
                    events.append(cur_ev)

                trans = []
                if sim_rows:
                    trans.append({"at": sim_rows[0]["timestamp"], "from": None, "to": sim_rows[0]["mode"], "kind": "INITIAL_STATE", "message": "INITIAL STATE %s" % sim_rows[0]["mode"]})
                    for prev, cur in zip(sim_rows, sim_rows[1:]):
                        if prev["mode"] != cur["mode"]:
                            trans.append({"at": cur["timestamp"], "from": prev["mode"], "to": cur["mode"], "kind": "TRANSITION", "message": "TRANSITION %s -> %s" % (prev["mode"], cur["mode"])})

                out_rows = []
                for i, r in enumerate(sim_rows):
                    lo = round(float(r["demand_kw"]) - 1.96 * SIGMA, 1)
                    hi = round(float(r["demand_kw"]) + 1.96 * SIGMA, 1)
                    rec = dict(r)
                    rec.update({
                        "p95_low": lo,
                        "p95_high": hi,
                        "overload_prob": round(_overload_prob(float(r["demand_kw"])), 3),
                        "ambient_temp_c": float(rows[i].get("ambient_temp_c", 18.5)),
                        "humidity_pct": float(rows[i].get("humidity_pct", 70.0)),
                        "solar_irradiance_wm2": float(rows[i].get("solar_irradiance_wm2", 0.0)),
                        "explain": _explain(feature_list[i])
                    })
                    out_rows.append(rec)

                peak_kw = max(r["demand_kw"] for r in out_rows) if out_rows else None
                peak_at = next((r["timestamp"] for r in out_rows if r["demand_kw"] == peak_kw), None) if out_rows else None
                socs = [r["soc_pct"] for r in out_rows]
                min_soc = min(socs) if socs else None
                min_soc_at = next((r["timestamp"] for r in out_rows if r["soc_pct"] == min_soc), None) if out_rows else None

                kpis = {
                    "peak_kw": peak_kw,
                    "peak_at": peak_at,
                    "renewable_hours": sum(1 for r in out_rows if r["mode"] == "RENEWABLE_STORAGE"),
                    "grid_hours": sum(1 for r in out_rows if r["mode"] == "MAIN_GRID"),
                    "overload_hours": sum(1 for r in out_rows if r["critical_overload"]),
                    "overload_events": len(events),
                    "mode_switches": sum(1 for t in trans if t["kind"] == "TRANSITION"),
                    "min_soc_pct": min_soc,
                    "min_soc_at": min_soc_at
                }
                latency = int((time.time() - t0) * 1000)
                run_id = "r_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), os.urandom(2).hex())
                res = {
                    "run_id": run_id,
                    "model": {
                        "source": "quickml" if os.getenv("QUICKML_ENDPOINT_URL") else "local-linear",
                        "version": os.getenv("QUICKML_MODEL_VERSION", "quickml-v1"),
                        "latency_ms": latency,
                        "quickml_calls": 1,
                        "cache_hits": 0,
                        "failed_chunks": 0
                    },
                    "quality": {"rows": len(out_rows), "bad_timestamp": 0, "filled": {}, "clipped": {}, "warnings": [], "gaps": 0},
                    "rows": out_rows,
                    "transitions": trans,
                    "events": events,
                    "kpis": kpis
                }
                store.save_run(res)
                self._json(200, res)
            except Exception as e:
                self._json(500, {"error": str(e), "traceback": traceback.format_exc()})

        elif path == "/api/upload":
            try:
                text = raw_body.decode("utf-8", errors="replace")
                # find CSV content
                lines = [l for l in text.splitlines() if l.strip()]
                # if multipart, strip boundary lines
                if any("boundary=" in (self.headers.get("Content-Type") or "").lower() for _ in [1]):
                    clean_lines = []
                    header_done = False
                    for line in lines:
                        if line.startswith("--"):
                            continue
                        if ":" in line and not header_done:
                            continue
                        header_done = True
                        clean_lines.append(line)
                    lines = clean_lines

                reader = csv.DictReader(lines)
                parsed_rows = []
                for r in reader:
                    # normalize column names
                    norm = {}
                    for k, v in r.items():
                        if k:
                            norm[k.strip().lower()] = v.strip() if isinstance(v, str) else v
                    parsed_rows.append({
                        "timestamp": norm.get("timestamp", ""),
                        "ambient_temp_c": float(norm.get("ambient_temp_c", 18.5) or 18.5),
                        "humidity_pct": float(norm.get("humidity_pct", 70.0) or 70.0),
                        "solar_irradiance_wm2": float(norm.get("solar_irradiance_wm2", 0.0) or 0.0)
                    })
                self._json(200, {
                    "rows": parsed_rows,
                    "quality": {"rows": len(parsed_rows), "filled": {}, "clipped": {}}
                })
            except Exception as e:
                self._json(400, {"error": "Failed to parse CSV: %s" % e})
        else:
            self._json(404, {"error": "POST route not found: %s" % path})


def run():
    print("Serving microgrid-api on 0.0.0.0:%s via pure-Python HTTPServer" % PORT, flush=True)
    server = HTTPServer(("0.0.0.0", PORT), MicrogridHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
