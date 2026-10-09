from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_SETTING = Path(os.getenv("DATABASE_PATH", "data/climateguard_platform.sqlite3"))
DB_PATH = DB_SETTING if DB_SETTING.is_absolute() else BASE_DIR / DB_SETTING
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize() -> None:
    with db() as cx:
        cx.executescript("""
        CREATE TABLE IF NOT EXISTS locations (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, admin1 TEXT, country TEXT NOT NULL DEFAULT 'IND',
            latitude REAL, longitude REAL, area_sq_km REAL, boundary_id TEXT, boundary_year TEXT,
            geometry TEXT, geometry_simplified TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, location_id TEXT NOT NULL REFERENCES locations(id),
            category TEXT NOT NULL, observed_on TEXT NOT NULL, observed_to TEXT,
            value REAL, unit TEXT, source TEXT NOT NULL, product TEXT, resolution TEXT,
            quality TEXT, metadata TEXT, retrieved_at TEXT NOT NULL,
            UNIQUE(location_id,category,observed_on,observed_to,source,product)
        );
        CREATE INDEX IF NOT EXISTS ix_obs_location_category_date ON observations(location_id,category,observed_on);
        CREATE TABLE IF NOT EXISTS source_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, status TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, records INTEGER DEFAULT 0, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS kits (
            id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id), token_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kit_id TEXT NOT NULL REFERENCES kits(id), message_id TEXT NOT NULL,
            observed_at TEXT NOT NULL, received_at TEXT NOT NULL, measurements TEXT NOT NULL, quality TEXT,
            UNIQUE(kit_id,message_id)
        );
        CREATE TABLE IF NOT EXISTS model_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL, location_id TEXT NOT NULL, trained_at TEXT NOT NULL,
            cutoff TEXT, training_start TEXT, training_end TEXT, metrics TEXT NOT NULL,
            feature_names TEXT NOT NULL, artifact_path TEXT NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, location_id TEXT NOT NULL REFERENCES locations(id),
            prediction_at TEXT NOT NULL, period_start TEXT, period_end TEXT, score REAL, risk_level TEXT,
            model_version TEXT NOT NULL, explanation TEXT, quality TEXT
        );
        """)
