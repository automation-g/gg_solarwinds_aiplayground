"""Sync latest ticket data — fetch new + updated tickets."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from api_client import fetch_incident_detail, _get, PER_PAGE

DB_PATH = Path(__file__).parent / "ticket_history_slim.db"


def sync_recent(days_back: int | None = None, on_progress: Callable | None = None) -> dict:
    """Fetch new and updated tickets since the last sync.

    Args:
        days_back: Number of days to look back. None = auto-detect from DB.
        on_progress: Callback function(message: str) for progress updates.

    Returns summary dict with counts.
    """
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    now = datetime.now()

    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    # Determine sync start date
    if days_back is not None:
        since = now - timedelta(days=days_back)
    else:
        # Check last sync timestamp
        row = conn.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()
        if row and row[0]:
            since = datetime.strptime(row[0][:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=1)  # 1hr overlap for safety
            progress(f"Last sync: {row[0][:16]}")
        else:
            # First sync — use latest ticket date
            row = conn.execute("SELECT MAX(substr(created_at, 1, 10)) FROM incidents").fetchone()
            if row and row[0]:
                since = datetime.strptime(row[0], "%Y-%m-%d") - timedelta(days=1)
            else:
                since = now - timedelta(days=7)

    since_str = since.strftime("%Y-%m-%dT00:00:00+04:00")
    now_str = now.strftime("%Y-%m-%dT23:59:59+04:00")

    # Step 1: Fetch recently created tickets
    progress(f"Step 1/3: Listing new tickets since {since.strftime('%Y-%m-%d')}...")
    new_records = _fetch_paginated("/incidents.json", {
        "per_page": PER_PAGE,
        "sort_by": "created_at",
        "sort_order": "DESC",
        "created[]": "Select Date Range",
        "created_custom_gte": since_str,
        "created_custom_lte": now_str,
    })
    progress(f"Step 1/3: Found {len(new_records)} new tickets")

    # Step 2: Fetch recently updated tickets
    progress(f"Step 2/3: Listing updated tickets since {since.strftime('%Y-%m-%d')}...")
    updated_records = _fetch_paginated("/incidents.json", {
        "per_page": PER_PAGE,
        "sort_by": "updated_at",
        "sort_order": "DESC",
        "updated[]": "Select Date Range",
        "updated_custom_gte": since_str,
        "updated_custom_lte": now_str,
    })
    progress(f"Step 2/3: Found {len(updated_records)} updated tickets")

    # Deduplicate
    all_records = {}
    for r in new_records + updated_records:
        all_records[r.get("id")] = r

    # Split into new vs existing
    existing_ids = set()
    for (inc_id,) in conn.execute(
        "SELECT id FROM incidents WHERE id IN ({})".format(
            ",".join("?" for _ in all_records)
        ), list(all_records.keys())
    ).fetchall():
        existing_ids.add(inc_id)

    new_ids = [k for k in all_records if k not in existing_ids]
    update_ids = [k for k in all_records if k in existing_ids]

    progress(f"Step 3/3: Processing {len(new_ids)} new + {len(update_ids)} updated tickets...")

    new_count = 0
    updated_count = 0
    total_to_process = len(new_ids) + len(update_ids)
    processed = 0

    def _progress_msg():
        pct = int(processed / max(total_to_process, 1) * 100)
        return f"Step 3/3: {processed}/{total_to_process} ({pct}%) — {new_count} new, {updated_count} updated"

    # New tickets — fetch full details (need all fields)
    for inc_id in new_ids:
        detail = fetch_incident_detail(inc_id)
        if detail:
            _upsert_full(conn, detail)
            new_count += 1
        processed += 1
        progress(_progress_msg())
        if processed % 10 == 0:
            conn.commit()

    # Existing tickets — update from listing data (no extra API call)
    for inc_id in update_ids:
        r = all_records[inc_id]
        conn.execute("""
            UPDATE incidents SET
                state = ?, priority = ?, assignee_name = ?,
                updated_at = ?, resolved_at = ?,
                is_escalated = ?, fetched_at = ?
            WHERE id = ?
        """, (
            r.get("state", ""),
            r.get("priority", ""),
            _safe_get(r, "assignee", "name"),
            r.get("updated_at", ""),
            r.get("resolved_at", ""),
            1 if r.get("is_escalated") else 0,
            datetime.now().isoformat(),
            inc_id,
        ))
        updated_count += 1
        processed += 1
        if processed % 20 == 0:
            conn.commit()
            progress(_progress_msg())

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]

    # Save sync timestamp
    conn.execute(
        "INSERT INTO sync_log (synced_at, since, new_count, updated_count, total) VALUES (?, ?, ?, ?, ?)",
        (now.isoformat(), since.strftime("%Y-%m-%d %H:%M"), new_count, updated_count, total),
    )
    conn.commit()
    conn.close()

    return {
        "fetched": len(all_records),
        "new": new_count,
        "updated": updated_count,
        "total": total,
        "since": since.strftime("%Y-%m-%d %H:%M"),
    }


def _fetch_paginated(path: str, params: dict) -> list[dict]:
    """Fetch all pages from an API endpoint."""
    all_records = []
    page = 1
    while True:
        params["page"] = page
        resp = _get(path, params)
        total_pages = int(resp.headers.get("X-Total-Pages", "1"))
        data = resp.json()
        if isinstance(data, list):
            if not data:
                break
            all_records.extend(data)
        if page >= total_pages:
            break
        page += 1
    return all_records


def _upsert_full(conn: sqlite3.Connection, detail: dict) -> None:
    """Insert or replace a ticket with all fields from detail response."""
    resolved_by = detail.get("resolved_by") or {}
    created_by = detail.get("created_by") or {}

    conn.execute("""
        INSERT OR REPLACE INTO incidents
        (id, number, name, state, priority, category, subcategory,
         assignee_name, requester_name, site, department,
         is_service_request, is_escalated,
         created_at, updated_at, due_at, resolved_at,
         description, resolution_description, origin,
         resolved_by_name, resolved_by_email,
         created_by_name, created_by_email, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        detail.get("id"),
        detail.get("number"),
        detail.get("name", ""),
        detail.get("state", ""),
        detail.get("priority", ""),
        _safe_get(detail, "category", "name"),
        _safe_get(detail, "subcategory", "name"),
        _safe_get(detail, "assignee", "name"),
        _safe_get(detail, "requester", "name"),
        _safe_get(detail, "site", "name"),
        _safe_get(detail, "department", "name"),
        1 if detail.get("is_service_request") else 0,
        1 if detail.get("is_escalated") else 0,
        detail.get("created_at", ""),
        detail.get("updated_at", ""),
        detail.get("due_at", ""),
        detail.get("resolved_at", ""),
        detail.get("description_no_html", "") or "",
        detail.get("resolution_description", "") or "",
        detail.get("origin", "") or "",
        resolved_by.get("name", "") if isinstance(resolved_by, dict) else "",
        resolved_by.get("email", "") if isinstance(resolved_by, dict) else "",
        created_by.get("name", "") if isinstance(created_by, dict) else "",
        created_by.get("email", "") if isinstance(created_by, dict) else "",
        datetime.now().isoformat(),
    ))


def _safe_get(d: dict, *keys: str, default: str = "") -> str:
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current if current is not None else default
