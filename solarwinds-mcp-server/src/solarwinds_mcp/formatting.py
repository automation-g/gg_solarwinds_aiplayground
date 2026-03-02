"""Response formatting and statistics helpers for SolarWinds MCP tools."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any


def _strip_html(text: str | None) -> str | None:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return text
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_dt(value: str | None) -> datetime | None:
    """Try to parse an ISO-8601 datetime string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---- Incident formatting ----

def format_incident_summary(inc: dict[str, Any]) -> dict[str, Any]:
    """Extract key fields from a raw incident for a compact summary."""
    return {
        "id": inc.get("id"),
        "number": inc.get("number"),
        "name": inc.get("name"),
        "state": inc.get("state"),
        "priority": inc.get("priority"),
        "category": (inc.get("category") or {}).get("name"),
        "assignee": (inc.get("assignee") or {}).get("name"),
        "requester": (inc.get("requester") or {}).get("name"),
        "created_at": inc.get("created_at"),
        "updated_at": inc.get("updated_at"),
        "due_at": inc.get("due_at"),
    }


def format_incident_detail(inc: dict[str, Any]) -> dict[str, Any]:
    """Format a full incident detail response."""
    summary = format_incident_summary(inc)
    summary.update({
        "description": _strip_html(inc.get("description")),
        "resolution": _strip_html(inc.get("resolution")),
        "resolved_at": inc.get("resolved_at"),
        "site": (inc.get("site") or {}).get("name"),
        "department": (inc.get("department") or {}).get("name"),
        "custom_fields_values": inc.get("custom_fields_values"),
        "comments": [
            {
                "body": _strip_html(c.get("body")),
                "created_at": c.get("created_at"),
                "user": (c.get("user") or {}).get("name"),
                "is_private": c.get("is_private"),
            }
            for c in (inc.get("comments") or [])
        ],
        "assets": [
            {"id": a.get("id"), "name": a.get("name"), "asset_type": a.get("asset_type")}
            for a in (inc.get("assets") or [])
        ],
        "audit_archives": [
            {
                "message": a.get("message"),
                "created_at": a.get("created_at"),
                "user": (a.get("user") or {}).get("name"),
            }
            for a in (inc.get("audit_archives") or [])[:20]  # cap to keep output manageable
        ],
    })
    return summary


# ---- Statistics ----

def compute_incident_statistics(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate statistics from a list of incidents."""
    total = len(incidents)
    if total == 0:
        return {"total": 0, "message": "No incidents found matching the criteria."}

    state_counts = Counter(inc.get("state") for inc in incidents)
    priority_counts = Counter(inc.get("priority") for inc in incidents)
    category_counts = Counter(
        (inc.get("category") or {}).get("name", "Uncategorized")
        for inc in incidents
    )

    # Resolution time analysis
    resolution_times: list[float] = []
    overdue = 0
    now = datetime.now().astimezone()

    for inc in incidents:
        created = _parse_dt(inc.get("created_at"))
        resolved = _parse_dt(inc.get("resolved_at"))
        due = _parse_dt(inc.get("due_at"))

        if created and resolved:
            delta = (resolved - created).total_seconds() / 3600  # hours
            resolution_times.append(delta)

        if due:
            # Overdue: past due and not resolved, or resolved after due
            if resolved:
                if resolved > due:
                    overdue += 1
            elif now > due:
                overdue += 1

    avg_resolution_hours = (
        round(sum(resolution_times) / len(resolution_times), 1)
        if resolution_times
        else None
    )

    return {
        "total": total,
        "by_state": dict(state_counts.most_common()),
        "by_priority": dict(priority_counts.most_common()),
        "by_category": dict(category_counts.most_common(10)),
        "avg_resolution_hours": avg_resolution_hours,
        "resolved_count": len(resolution_times),
        "overdue_count": overdue,
    }


def compute_sla_performance(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute SLA compliance from incidents that have due dates."""
    with_due = [inc for inc in incidents if inc.get("due_at")]
    if not with_due:
        return {
            "total_with_sla": 0,
            "message": "No incidents with SLA due dates found.",
        }

    now = datetime.now().astimezone()
    met = 0
    breached = 0
    pending = 0

    by_priority: dict[str, dict[str, int]] = {}

    for inc in with_due:
        due = _parse_dt(inc.get("due_at"))
        resolved = _parse_dt(inc.get("resolved_at"))
        prio = inc.get("priority", "Unknown")

        if prio not in by_priority:
            by_priority[prio] = {"met": 0, "breached": 0, "pending": 0}

        if resolved:
            if resolved <= due:
                met += 1
                by_priority[prio]["met"] += 1
            else:
                breached += 1
                by_priority[prio]["breached"] += 1
        else:
            if now <= due:
                pending += 1
                by_priority[prio]["pending"] += 1
            else:
                breached += 1
                by_priority[prio]["breached"] += 1

    total = met + breached + pending
    compliance_pct = round((met / (met + breached)) * 100, 1) if (met + breached) > 0 else None

    return {
        "total_with_sla": len(with_due),
        "met": met,
        "breached": breached,
        "pending": pending,
        "compliance_percent": compliance_pct,
        "by_priority": by_priority,
    }


# ---- Generic formatters ----

def format_change(change: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": change.get("id"),
        "number": change.get("number"),
        "name": change.get("name"),
        "state": change.get("state"),
        "priority": change.get("priority"),
        "change_type": change.get("change_type"),
        "requester": (change.get("requester") or {}).get("name"),
        "assignee": (change.get("assignee") or {}).get("name"),
        "created_at": change.get("created_at"),
        "planned_start": change.get("planned_start_date"),
        "planned_end": change.get("planned_end_date"),
    }


def format_problem(problem: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": problem.get("id"),
        "number": problem.get("number"),
        "name": problem.get("name"),
        "state": problem.get("state"),
        "priority": problem.get("priority"),
        "assignee": (problem.get("assignee") or {}).get("name"),
        "root_cause": problem.get("root_cause"),
        "created_at": problem.get("created_at"),
    }


def format_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": asset.get("id"),
        "name": asset.get("name"),
        "asset_type": asset.get("asset_type"),
        "status": (asset.get("status") or {}).get("name"),
        "serial_number": asset.get("serial_number"),
        "manufacturer": asset.get("manufacturer"),
        "model": asset.get("model"),
        "owner": (asset.get("owner") or {}).get("name"),
        "site": (asset.get("site") or {}).get("name"),
    }


def format_solution(sol: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sol.get("id"),
        "name": sol.get("name"),
        "description": (sol.get("description") or "")[:500],
        "created_at": sol.get("created_at"),
        "updated_at": sol.get("updated_at"),
    }


def format_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": (user.get("role") or {}).get("name"),
        "department": (user.get("department") or {}).get("name"),
        "site": (user.get("site") or {}).get("name"),
        "disabled": user.get("disabled"),
    }


def format_category(cat: dict[str, Any]) -> dict[str, Any]:
    children = cat.get("children") or []
    return {
        "id": cat.get("id"),
        "name": cat.get("name"),
        "subcategories": [
            {"id": c.get("id"), "name": c.get("name")}
            for c in children
        ],
    }
