"""Fetch historical ticket data from SolarWinds and store in SQLite.

Usage:
    python fetch.py                    # Fetch all remaining months
    python fetch.py --month 2026-03    # Fetch a specific month
    python fetch.py --status           # Show progress summary
"""

from __future__ import annotations

import argparse
import calendar
from datetime import datetime, timedelta

from tqdm import tqdm

from api_client import (
    fetch_incidents_for_month,
    fetch_incident_detail,
    fetch_incident_audits,
    fetch_time_track_detail,
    BASE_URL,
)
from db import (
    init_db,
    get_conn,
    upsert_incident,
    upsert_time_track,
    insert_audit_entry,
    get_progress,
    set_progress,
    get_all_progress,
)


def generate_months(start_year: int = 2010, start_month: int = 1) -> list[str]:
    """Generate month keys from start_year-start_month to current month."""
    now = datetime.now()
    months = []
    y, m = start_year, start_month
    while (y, m) <= (now.year, now.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def fetch_month(month_key: str) -> None:
    """Fetch all data for a single month."""
    conn = get_conn()

    progress = get_progress(conn, month_key)
    if progress and progress["status"] == "done":
        print(f"[{month_key}] Already done, skipping.")
        conn.close()
        return

    year, month = int(month_key[:4]), int(month_key[5:])
    last_day = calendar.monthrange(year, month)[1]
    # Extend range by 4 hours (UAE is UTC+4) to capture boundary tickets
    prev_month_last = datetime(year, month, 1) - timedelta(days=1)
    start = f"{prev_month_last.strftime('%Y-%m-%d')}T20:00:00Z"
    end = f"{month_key}-{last_day:02d}T23:59:59Z"

    set_progress(conn, month_key, status="in_progress", started_at=datetime.utcnow().isoformat())

    # Phase 1: List incidents
    print(f"\n[{month_key}] Phase 1: Listing incidents...")
    raw_incidents = fetch_incidents_for_month(start, end)
    # Filter to only keep incidents with created_at in the target month
    incidents = [r for r in raw_incidents if r.get("created_at", "")[:7] == month_key]
    print(f"[{month_key}] Found {len(incidents)} incidents (fetched {len(raw_incidents)}, filtered to target month)")

    if not incidents:
        set_progress(conn, month_key, status="done", incident_count=0,
                     completed_at=datetime.utcnow().isoformat())
        conn.close()
        return

    # Save basic incident data
    for inc in incidents:
        upsert_incident(conn, inc)
    conn.commit()
    set_progress(conn, month_key, incident_count=len(incidents))

    # Phase 2: Fetch details + audit trails
    print(f"[{month_key}] Phase 2: Fetching details & audits...")
    detail_count = 0
    time_track_refs = []

    for inc in tqdm(incidents, desc=f"[{month_key}] Details", ncols=80):
        inc_id = inc.get("id", 0)

        # Fetch detail
        detail = fetch_incident_detail(inc_id)
        if detail:
            upsert_incident(conn, detail)
            detail_count += 1

            # Collect time track refs
            for tt in detail.get("time_tracks", []):
                href = tt.get("href", "")
                if href:
                    time_track_refs.append((inc_id, href))

        # Audits skipped for now (can fetch later)
        # audits = fetch_incident_audits(inc_id)
        # for audit in audits:
        #     insert_audit_entry(conn, inc_id, audit)

        # Commit every 50 incidents
        if detail_count % 50 == 0:
            conn.commit()

    conn.commit()
    set_progress(conn, month_key, detail_count=detail_count)

    # Time tracks skipped for now (can fetch later)
    # print(f"[{month_key}] Phase 3: Fetching {len(time_track_refs)} time tracks...")
    # tt_count = 0
    # for inc_id, href in tqdm(time_track_refs, desc=f"[{month_key}] Time tracks", ncols=80):
    #     tt_data = fetch_time_track_detail(href)
    #     if tt_data:
    #         upsert_time_track(conn, inc_id, tt_data)
    #         tt_count += 1
    # conn.commit()

    tt_count = 0
    set_progress(conn, month_key, status="done", time_track_count=tt_count,
                 detail_count=detail_count, completed_at=datetime.utcnow().isoformat())
    conn.commit()

    print(f"[{month_key}] Done: {len(incidents)} incidents, {detail_count} details (audits & time tracks deferred)")
    print(f"[{month_key}] Progress saved.", flush=True)
    conn.close()


def show_status() -> None:
    """Print fetch progress summary."""
    conn = get_conn()
    progress = get_all_progress(conn)
    conn.close()

    if not progress:
        print("No fetch progress recorded yet.")
        return

    total_incidents = 0
    total_tt = 0
    done = 0
    pending = 0

    print(f"\n{'Month':<10} {'Status':<12} {'Incidents':>10} {'Details':>10} {'TimeTracks':>10} {'Completed':<20}")
    print("-" * 82)
    for p in progress:
        print(f"{p['month_key']:<10} {p['status']:<12} {p['incident_count']:>10} {p['detail_count']:>10} {p['time_track_count']:>10} {(p['completed_at'] or ''):<20}")
        total_incidents += p["incident_count"]
        total_tt += p["time_track_count"]
        if p["status"] == "done":
            done += 1
        else:
            pending += 1

    print("-" * 82)
    print(f"Total: {done} months done, {pending} pending, {total_incidents} incidents, {total_tt} time tracks")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch historical SolarWinds ticket data")
    parser.add_argument("--month", help="Fetch a specific month (YYYY-MM)")
    parser.add_argument("--status", action="store_true", help="Show progress summary")
    args = parser.parse_args()

    init_db()

    if args.status:
        show_status()
        return

    if args.month:
        fetch_month(args.month)
    else:
        months = generate_months()
        print(f"Fetching {len(months)} months (2010-01 to present)...")
        for m in months:
            try:
                fetch_month(m)
            except Exception as e:
                conn = get_conn()
                set_progress(conn, m, status="error", error=str(e))
                conn.close()
                print(f"[{m}] ERROR: {e}")
                continue


if __name__ == "__main__":
    main()
