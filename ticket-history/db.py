"""SQLite database helpers for ticket history."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "ticket_history.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id              INTEGER PRIMARY KEY,
            number          INTEGER,
            name            TEXT,
            state           TEXT,
            priority        TEXT,
            category        TEXT,
            subcategory     TEXT,
            assignee_name   TEXT,
            requester_name  TEXT,
            site            TEXT,
            department      TEXT,
            is_service_request INTEGER,
            is_escalated    INTEGER,
            created_at      TEXT,
            updated_at      TEXT,
            due_at          TEXT,
            resolved_at     TEXT,
            raw_json        TEXT,
            fetched_at      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_incidents_state ON incidents(state);
        CREATE INDEX IF NOT EXISTS idx_incidents_priority ON incidents(priority);
        CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);
        CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at);
        CREATE INDEX IF NOT EXISTS idx_incidents_assignee ON incidents(assignee_name);
        CREATE INDEX IF NOT EXISTS idx_incidents_number ON incidents(number);

        CREATE TABLE IF NOT EXISTS time_tracks (
            id              INTEGER PRIMARY KEY,
            incident_id     INTEGER,
            creator_name    TEXT,
            minutes         INTEGER,
            name            TEXT,
            created_at      TEXT,
            raw_json        TEXT,
            fetched_at      TEXT,
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        );

        CREATE INDEX IF NOT EXISTS idx_tt_incident ON time_tracks(incident_id);
        CREATE INDEX IF NOT EXISTS idx_tt_creator ON time_tracks(creator_name);

        CREATE TABLE IF NOT EXISTS audit_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id     INTEGER,
            message         TEXT,
            user_name       TEXT,
            created_at      TEXT,
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        );

        CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_entries(incident_id);

        CREATE TABLE IF NOT EXISTS fetch_progress (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            month_key       TEXT UNIQUE,
            status          TEXT DEFAULT 'pending',
            incident_count  INTEGER DEFAULT 0,
            detail_count    INTEGER DEFAULT 0,
            time_track_count INTEGER DEFAULT 0,
            started_at      TEXT,
            completed_at    TEXT,
            error           TEXT
        );
    """)
    conn.commit()
    conn.close()


def _safe_get(d: dict, *keys: str, default: str = "") -> str:
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current if current is not None else default


def upsert_incident(conn: sqlite3.Connection, r: dict) -> None:
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO incidents
        (id, number, name, state, priority, category, subcategory,
         assignee_name, requester_name, site, department,
         is_service_request, is_escalated,
         created_at, updated_at, due_at, resolved_at, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        r.get("id"),
        r.get("number"),
        r.get("name", ""),
        r.get("state", ""),
        r.get("priority", ""),
        _safe_get(r, "category", "name"),
        _safe_get(r, "subcategory", "name"),
        _safe_get(r, "assignee", "name"),
        _safe_get(r, "requester", "name"),
        _safe_get(r, "site", "name"),
        _safe_get(r, "department", "name"),
        1 if r.get("is_service_request") else 0,
        1 if r.get("is_escalated") else 0,
        r.get("created_at", ""),
        r.get("updated_at", ""),
        r.get("due_at", ""),
        r.get("resolved_at", ""),
        json.dumps(r),
        now,
    ))


def upsert_time_track(conn: sqlite3.Connection, incident_id: int, tt: dict) -> None:
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO time_tracks
        (id, incident_id, creator_name, minutes, name, created_at, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tt.get("id"),
        incident_id,
        _safe_get(tt, "creator", "name"),
        tt.get("minutes", 0),
        tt.get("name", ""),
        tt.get("created_at", ""),
        json.dumps(tt),
        now,
    ))


def insert_audit_entry(conn: sqlite3.Connection, incident_id: int, audit: dict) -> None:
    conn.execute("""
        INSERT INTO audit_entries (incident_id, message, user_name, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        incident_id,
        audit.get("message", ""),
        _safe_get(audit, "user", "name"),
        audit.get("created_at", ""),
    ))


def get_progress(conn: sqlite3.Connection, month_key: str) -> dict | None:
    cur = conn.execute("SELECT * FROM fetch_progress WHERE month_key = ?", (month_key,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def set_progress(conn: sqlite3.Connection, month_key: str, **kwargs) -> None:
    existing = get_progress(conn, month_key)
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(
            f"UPDATE fetch_progress SET {sets} WHERE month_key = ?",
            (*kwargs.values(), month_key),
        )
    else:
        kwargs["month_key"] = month_key
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        conn.execute(
            f"INSERT INTO fetch_progress ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
    conn.commit()


def incident_exists(conn: sqlite3.Connection, inc_id: int) -> bool:
    cur = conn.execute("SELECT 1 FROM incidents WHERE id = ?", (inc_id,))
    return cur.fetchone() is not None


def get_all_progress(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT * FROM fetch_progress ORDER BY month_key")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
