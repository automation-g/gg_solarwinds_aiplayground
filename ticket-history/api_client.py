"""Synchronous HTTP client for SolarWinds Service Desk API (historical fetch)."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("SOLARWINDS_API_TOKEN", "")
REGION = os.getenv("SOLARWINDS_REGION", "us").lower().strip()
PER_PAGE = int(os.getenv("SOLARWINDS_PER_PAGE", "100"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.3"))

BASE_URL = "https://apieu.samanage.com" if REGION == "eu" else "https://api.samanage.com"
HEADERS = {
    "X-Samanage-Authorization": f"Bearer {API_TOKEN}",
    "Accept": "application/vnd.samanage.v2.1+json",
    "Content-Type": "application/json",
}


def _get(path: str, params: dict[str, Any] | None = None) -> httpx.Response:
    for attempt in range(5):
        resp = httpx.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=60.0)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"  Rate limited, waiting {wait}s (attempt {attempt+1}/5)...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def fetch_incidents_for_month(start: str, end: str) -> list[dict[str, Any]]:
    """Fetch all incidents created in a date range (no page limit)."""
    params: dict[str, Any] = {
        "per_page": PER_PAGE,
        "sort_by": "created_at",
        "sort_order": "ASC",
        "created[]": "Select Date Range",
        "created_custom_gte": start,
        "created_custom_lte": end,
    }
    all_records: list[dict[str, Any]] = []
    page = 1

    while True:
        params["page"] = page
        resp = _get("/incidents.json", params)
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


def fetch_incident_detail(inc_id: int) -> dict[str, Any] | None:
    """Fetch full details for a single incident."""
    try:
        time.sleep(REQUEST_DELAY)
        resp = _get(f"/incidents/{inc_id}.json")
        return resp.json()
    except Exception as e:
        print(f"  Error fetching detail for {inc_id}: {e}")
        return None


def fetch_incident_audits(inc_id: int) -> list[dict[str, Any]]:
    """Fetch audit trail for a single incident."""
    try:
        time.sleep(REQUEST_DELAY)
        resp = _get(f"/incidents/{inc_id}/audits.json")
        return resp.json() if isinstance(resp.json(), list) else []
    except Exception as e:
        print(f"  Error fetching audits for {inc_id}: {e}")
        return []


def fetch_time_track_detail(href: str) -> dict[str, Any] | None:
    """Fetch a single time track by its href path."""
    try:
        time.sleep(REQUEST_DELAY)
        path = href.replace(BASE_URL, "")
        resp = _get(path)
        return resp.json()
    except Exception as e:
        print(f"  Error fetching time track {href}: {e}")
        return None
