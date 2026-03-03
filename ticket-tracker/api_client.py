"""Synchronous HTTP client for SolarWinds Service Desk API."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("SOLARWINDS_API_TOKEN", "")
REGION = os.getenv("SOLARWINDS_REGION", "us").lower().strip()
PER_PAGE = int(os.getenv("SOLARWINDS_PER_PAGE", "100"))
MAX_PAGES = int(os.getenv("SOLARWINDS_MAX_PAGES", "50"))

BASE_URL = "https://apieu.samanage.com" if REGION == "eu" else "https://api.samanage.com"
HEADERS = {
    "X-Samanage-Authorization": f"Bearer {API_TOKEN}",
    "Accept": "application/vnd.samanage.v2.1+json",
    "Content-Type": "application/json",
}


def _get(path: str, params: dict[str, Any] | None = None) -> httpx.Response:
    resp = httpx.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp


def _get_paginated(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if params is None:
        params = {}
    params.setdefault("per_page", PER_PAGE)

    all_records: list[dict[str, Any]] = []
    page = 1

    while page <= MAX_PAGES:
        params["page"] = page
        resp = _get(path, params)
        total_pages = int(resp.headers.get("X-Total-Pages", "1"))
        data = resp.json()

        if isinstance(data, list):
            if not data:
                break
            all_records.extend(data)
        else:
            return [data]

        if page >= total_pages:
            break
        page += 1

    return all_records


def fetch_incidents(created_after: str, created_before: str) -> list[dict[str, Any]]:
    """Fetch all incidents in a date range, excluding Internal category."""
    params: dict[str, Any] = {
        "sort_by": "created_at",
        "sort_order": "DESC",
        "created[]": "Select Date Range",
        "created_custom_gte": created_after,
        "created_custom_lte": created_before,
    }
    records = _get_paginated("/incidents.json", params)
    # Exclude Internal category tickets
    return [
        r for r in records
        if (r.get("category", {}) or {}).get("name", "").strip().lower() != "internal"
    ]


def fetch_time_tracks(incidents: list[dict[str, Any]], max_workers: int = 10) -> list[dict[str, Any]]:
    """Fetch time track details for incidents that have them, using parallel requests."""
    # Collect all time track URLs from incidents that have time_tracks
    track_refs: list[tuple[int, str]] = []
    for inc in incidents:
        inc_id = inc.get("id", 0)
        for tt in inc.get("time_tracks", []):
            href = tt.get("href", "")
            if href:
                # Convert full URL to path
                path = href.replace(BASE_URL, "")
                track_refs.append((inc_id, path))

    if not track_refs:
        return []

    results: list[dict[str, Any]] = []

    def _fetch_track(ref: tuple[int, str]) -> dict[str, Any] | None:
        inc_id, path = ref
        try:
            resp = _get(path)
            data = resp.json()
            data["_incident_id"] = inc_id
            return data
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_track, ref): ref for ref in track_refs}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                results.append(result)

    return results


def fetch_incidents_with_details(incidents: list[dict[str, Any]], max_workers: int = 10) -> list[dict[str, Any]]:
    """Fetch full details (including time_tracks list) for each incident in parallel."""

    def _fetch_detail(inc: dict[str, Any]) -> dict[str, Any]:
        inc_id = inc.get("id", 0)
        try:
            resp = _get(f"/incidents/{inc_id}.json")
            return resp.json()
        except Exception:
            return inc

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_fetch_detail, incidents))

    return results


def safe_get(d: dict, *keys: str, default: str = "") -> str:
    """Safely navigate nested dicts."""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current if current is not None else default
