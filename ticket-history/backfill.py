"""Backfill audits and time tracks for incidents already in the DB (parallel)."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from api_client import fetch_incident_detail, fetch_incident_audits, fetch_time_track_detail, BASE_URL
from db import get_conn, upsert_time_track, insert_audit_entry

DB_PATH = Path(__file__).parent / "ticket_history.db"
MAX_WORKERS = 10

MONTHS_TO_BACKFILL = [
    "2025-08", "2025-09", "2025-10", "2025-11",
    "2025-12", "2026-01", "2026-02", "2026-03",
]


def _fetch_audits_for_incident(inc_id: int) -> tuple[int, list[dict]]:
    try:
        audits = fetch_incident_audits(inc_id)
        return (inc_id, audits)
    except Exception:
        return (inc_id, [])


def _fetch_tt_for_incident(inc_id: int) -> tuple[int, list[dict]]:
    """Fetch detail to get time track hrefs, then fetch each time track."""
    try:
        detail = fetch_incident_detail(inc_id)
        if not detail:
            return (inc_id, [])
        tracks = []
        for tt in detail.get("time_tracks", []):
            href = tt.get("href", "")
            if href:
                tt_data = fetch_time_track_detail(href)
                if tt_data:
                    tracks.append(tt_data)
        return (inc_id, tracks)
    except Exception:
        return (inc_id, [])


def backfill_month(month_key: str) -> None:
    conn = get_conn()

    # Get all incident IDs for this month
    rows = conn.execute(
        "SELECT id FROM incidents WHERE created_at LIKE ? ORDER BY id",
        (f"{month_key}%",),
    ).fetchall()
    inc_ids = [r[0] for r in rows]

    # Check which already have audits
    have_audits = set()
    if inc_ids:
        for (inc_id,) in conn.execute(
            "SELECT DISTINCT incident_id FROM audit_entries WHERE incident_id IN ({})".format(
                ",".join("?" for _ in inc_ids)
            ),
            inc_ids,
        ).fetchall():
            have_audits.add(inc_id)

    # Check which already have time tracks
    have_tt = set()
    if inc_ids:
        for (inc_id,) in conn.execute(
            "SELECT DISTINCT incident_id FROM time_tracks WHERE incident_id IN ({})".format(
                ",".join("?" for _ in inc_ids)
            ),
            inc_ids,
        ).fetchall():
            have_tt.add(inc_id)

    need_audits = [i for i in inc_ids if i not in have_audits]
    need_tt = [i for i in inc_ids if i not in have_tt]

    print(f"\n[{month_key}] {len(inc_ids)} incidents | need audits: {len(need_audits)} | need time tracks: {len(need_tt)}")

    if not need_audits and not need_tt:
        print(f"[{month_key}] Nothing to backfill.")
        conn.close()
        return

    # Fetch audits in parallel
    if need_audits:
        print(f"[{month_key}] Fetching audits ({MAX_WORKERS} workers)...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_audits_for_incident, inc_id): inc_id for inc_id in need_audits}
            done_count = 0
            for fut in tqdm(as_completed(futures), total=len(futures), desc=f"[{month_key}] Audits", ncols=80):
                inc_id, audits = fut.result()
                for audit in audits:
                    insert_audit_entry(conn, inc_id, audit)
                done_count += 1
                if done_count % 100 == 0:
                    conn.commit()
        conn.commit()

    # Fetch time tracks in parallel
    if need_tt:
        print(f"[{month_key}] Fetching time tracks ({MAX_WORKERS} workers)...")
        tt_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_tt_for_incident, inc_id): inc_id for inc_id in need_tt}
            done_count = 0
            for fut in tqdm(as_completed(futures), total=len(futures), desc=f"[{month_key}] TT", ncols=80):
                inc_id, tracks = fut.result()
                for tt_data in tracks:
                    upsert_time_track(conn, inc_id, tt_data)
                    tt_count += 1
                done_count += 1
                if done_count % 100 == 0:
                    conn.commit()
        conn.commit()
        print(f"[{month_key}] Saved {tt_count} time tracks")

    print(f"[{month_key}] Backfill complete.")
    conn.close()


def main() -> None:
    for m in MONTHS_TO_BACKFILL:
        try:
            backfill_month(m)
        except Exception as e:
            print(f"[{m}] ERROR: {e}")
            continue
    print("\nAll backfill done!")


if __name__ == "__main__":
    main()
