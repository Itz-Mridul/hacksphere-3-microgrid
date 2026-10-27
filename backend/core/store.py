"""SQLite persistence for runs, rows, alerts, logs, usage."""
import json
import os
import sqlite3
import threading
import time
import uuid


def db_path():
    if (
        os.getenv("X_ZOHO_CATALYST_LISTEN_PORT")
        or os.getenv("PORT")
        or os.getenv("X_ZOHO_CATALYST_RESOURCE_ID")
    ):
        base = "/tmp"
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
        try:
            if not os.path.isdir(base):
                os.makedirs(base)
        except OSError:
            base = "/tmp"
    return os.path.join(base, "hacksphere3.db")


class Store(object):
    def __init__(self, path=None):
        self.path = path or db_path()
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        with self.lock:
            c = self.conn
            c.executescript(
                """
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
                CREATE TABLE IF NOT EXISTS forecast_row (
                    run_id TEXT,
                    ts TEXT,
                    demand_kw REAL,
                    mode TEXT,
                    critical_overload INTEGER,
                    soc_pct REAL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS alert_event (
                    run_id TEXT,
                    start_ts TEXT,
                    end_ts TEXT,
                    hours INTEGER,
                    peak_kw REAL
                );
                CREATE TABLE IF NOT EXISTS system_log (
                    ts REAL,
                    level TEXT,
                    component TEXT,
                    message TEXT
                );
                CREATE TABLE IF NOT EXISTS usage_counter (
                    day TEXT PRIMARY KEY,
                    quickml_calls INTEGER
                );
                """
            )
            c.commit()

    def log(self, level, component, message):
        with self.lock:
            self.conn.execute(
                "INSERT INTO system_log(ts, level, component, message) VALUES (?,?,?,?)",
                (time.time(), level, component, message),
            )
            self.conn.commit()

    def new_run_id(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return "r_%s_%s" % (stamp, uuid.uuid4().hex[:4])

    def save_run(self, result):
        run_id = result["run_id"]
        kpis = result.get("kpis") or {}
        with self.lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO forecast_run(
                    run_id, created_at, n_rows, model_source, model_version,
                    latency_ms, quickml_calls, peak_kw, overload_hours, renewable_hours, payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    time.time(),
                    len(result.get("rows") or []),
                    (result.get("model") or {}).get("source"),
                    (result.get("model") or {}).get("version"),
                    (result.get("model") or {}).get("latency_ms"),
                    (result.get("model") or {}).get("quickml_calls", 0),
                    kpis.get("peak_kw"),
                    kpis.get("overload_hours"),
                    kpis.get("renewable_hours"),
                    json.dumps(result),
                ),
            )
            self.conn.execute("DELETE FROM forecast_row WHERE run_id=?", (run_id,))
            for r in result.get("rows") or []:
                self.conn.execute(
                    """INSERT INTO forecast_row(run_id, ts, demand_kw, mode, critical_overload, soc_pct, reason)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        r.get("timestamp"),
                        r.get("demand_kw"),
                        r.get("mode"),
                        1 if r.get("critical_overload") else 0,
                        r.get("soc_pct"),
                        r.get("reason"),
                    ),
                )
            self.conn.execute("DELETE FROM alert_event WHERE run_id=?", (run_id,))
            for e in result.get("events") or []:
                self.conn.execute(
                    "INSERT INTO alert_event(run_id, start_ts, end_ts, hours, peak_kw) VALUES (?,?,?,?,?)",
                    (run_id, e.get("start"), e.get("end"), e.get("hours"), e.get("peak_kw")),
                )
            self.conn.commit()

    def history(self, limit=20):
        with self.lock:
            rows = self.conn.execute(
                """SELECT run_id, created_at, n_rows, model_source, peak_kw, overload_hours, renewable_hours
                   FROM forecast_run ORDER BY created_at DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "run_id": r["run_id"],
                    "created_at": r["created_at"],
                    "n_rows": r["n_rows"],
                    "model_source": r["model_source"],
                    "peak_kw": r["peak_kw"],
                    "overload_hours": r["overload_hours"],
                    "renewable_hours": r["renewable_hours"],
                }
            )
        return out

    def get_run(self, run_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT payload_json FROM forecast_run WHERE run_id=?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def recent_logs(self, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT ts, level, component, message FROM system_log ORDER BY ts DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {"ts": r["ts"], "level": r["level"], "component": r["component"], "message": r["message"]}
            for r in rows
        ]

    def add_calls(self, n):
        day = time.strftime("%Y-%m-%d")
        with self.lock:
            cur = self.conn.execute("SELECT quickml_calls FROM usage_counter WHERE day=?", (day,)).fetchone()
            if cur is None:
                self.conn.execute("INSERT INTO usage_counter(day, quickml_calls) VALUES (?,?)", (day, int(n)))
            else:
                self.conn.execute(
                    "UPDATE usage_counter SET quickml_calls = quickml_calls + ? WHERE day=?",
                    (int(n), day),
                )
            self.conn.commit()

    def calls_today(self):
        day = time.strftime("%Y-%m-%d")
        with self.lock:
            cur = self.conn.execute("SELECT quickml_calls FROM usage_counter WHERE day=?", (day,)).fetchone()
        return int(cur["quickml_calls"]) if cur else 0


class UsageAdapter(object):
    def __init__(self, store):
        self.store = store

    def add(self, n):
        self.store.add_calls(n)
