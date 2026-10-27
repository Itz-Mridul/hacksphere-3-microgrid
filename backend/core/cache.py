"""In-memory prediction cache plus SQLite persistence."""
import sqlite3
import threading
import time


class PredCache(object):
    def __init__(self, conn, lock):
        self._mem = {}
        self._conn = conn
        self._lock = lock
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS pred_cache (
                    key TEXT PRIMARY KEY,
                    demand_kw REAL NOT NULL,
                    model_version TEXT,
                    created_at REAL
                )"""
            )
            self._conn.commit()
            rows = self._conn.execute("SELECT key, demand_kw FROM pred_cache").fetchall()
            for k, v in rows:
                self._mem[k] = float(v)

    def get(self, key):
        return self._mem.get(key)

    def put(self, key, demand_kw, model_version):
        self._mem[key] = float(demand_kw)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pred_cache(key, demand_kw, model_version, created_at) VALUES (?,?,?,?)",
                (key, float(demand_kw), model_version, time.time()),
            )
            self._conn.commit()

    def stats(self):
        return {"size": len(self._mem)}
