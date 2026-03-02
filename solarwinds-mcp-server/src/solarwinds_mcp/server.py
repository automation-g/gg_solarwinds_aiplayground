"""FastMCP server with all 11 read-only SolarWinds Service Desk tools."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import PaginatedResult, SolarWindsClient, SolarWindsClientError
from .config import SolarWindsConfig
from .formatting import (
    compute_incident_statistics,
    compute_sla_performance,
    format_asset,
    format_category,
    format_change,
    format_incident_detail,
    format_incident_summary,
    format_problem,
    format_solution,
    format_user,
)

mcp = FastMCP("SolarWinds Service Desk")

_config = SolarWindsConfig()
_client = SolarWindsClient(_config)


def _json(obj: Any) -> str:
    """Serialize to pretty JSON for Claude-friendly output."""
    return json.dumps(obj, indent=2, default=str)


def _error(e: Exception) -> str:
    if isinstance(e, SolarWindsClientError):
        return _json({"error": True, "status_code": e.status_code, "detail": e.detail})
    return _json({"error": True, "detail": str(e)})


def _truncation_fields(result: PaginatedResult) -> dict[str, Any]:
    """Build truncation metadata fields from a PaginatedResult."""
    fields: dict[str, Any] = {}
    if result.total_count is not None:
        fields["total_available"] = result.total_count
    if result.is_truncated:
        fields["is_truncated"] = True
        fields["warning"] = (
            f"Results were truncated. Showing {len(result.records)} of "
            f"{result.total_count or 'unknown'} total records."
        )
    else:
        fields["is_truncated"] = False
    return fields


# ───────────────────── 1. search_incidents ─────────────────────


@mcp.tool()
async def search_incidents(
    state: list[str] | None = None,
    priority: list[str] | None = None,
    category: str | None = None,
    assignee: str | None = None,
    keyword: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> str:
    """Search and filter incidents in SolarWinds Service Desk.

    Args:
        state: Filter by state(s), e.g. ["New", "Assigned", "Awaiting Input"]
        priority: Filter by priority(ies), e.g. ["Critical", "High"]
        category: Filter by category name
        assignee: Filter by assignee name
        keyword: Search incidents by title/name keyword
        created_after: ISO-8601 date, e.g. "2024-01-01T00:00:00Z"
        created_before: ISO-8601 date, e.g. "2024-12-31T23:59:59Z"

    Returns:
        JSON list of incident summaries with id, name, state, priority, category, assignee, dates.
    """
    try:
        result = await _client.list_incidents(
            state=state,
            priority=priority,
            category=category,
            assignee=assignee,
            keyword=keyword,
            created_after=created_after,
            created_before=created_before,
        )
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "incidents": [format_incident_summary(i) for i in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 2. get_incident_details ─────────────────────


@mcp.tool()
async def get_incident_details(incident_id: int) -> str:
    """Get full details for a specific incident including description, comments, assets, and audit trail.

    Args:
        incident_id: The numeric ID of the incident.

    Returns:
        JSON with full incident detail.
    """
    try:
        inc = await _client.get_incident(incident_id)
        return _json(format_incident_detail(inc))
    except Exception as e:
        return _error(e)


# ───────────────────── 3. get_incident_statistics ─────────────────────


@mcp.tool()
async def get_incident_statistics(
    state: list[str] | None = None,
    priority: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> str:
    """Get aggregate incident statistics: counts by state/priority/category, average resolution time, overdue count.

    Args:
        state: Filter by state(s) before computing stats
        priority: Filter by priority(ies) before computing stats
        created_after: ISO-8601 start date for time range
        created_before: ISO-8601 end date for time range

    Returns:
        JSON with total count, breakdowns by state/priority/category, avg resolution hours, overdue count.
    """
    try:
        result = await _client.list_incidents(
            state=state,
            priority=priority,
            created_after=created_after,
            created_before=created_before,
        )
        stats = compute_incident_statistics(result.records)
        if result.is_truncated:
            stats["data_truncated"] = True
            stats["data_truncated_warning"] = (
                f"Statistics computed from {len(result.records)} of "
                f"{result.total_count or 'unknown'} total records."
            )
        return _json(stats)
    except Exception as e:
        return _error(e)


# ───────────────────── 4. list_changes ─────────────────────


@mcp.tool()
async def list_changes(
    state: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> str:
    """List change requests filtered by state and date.

    Args:
        state: Filter by state(s), e.g. ["Requested", "Approved", "Implemented"]
        created_after: ISO-8601 start date
        created_before: ISO-8601 end date

    Returns:
        JSON list of change request summaries.
    """
    try:
        result = await _client.list_changes(
            state=state,
            created_after=created_after,
            created_before=created_before,
        )
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "changes": [format_change(c) for c in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 5. list_problems ─────────────────────


@mcp.tool()
async def list_problems(
    state: list[str] | None = None,
) -> str:
    """List problem records for root cause tracking.

    Args:
        state: Filter by state(s), e.g. ["Open", "Known Error", "Resolved"]

    Returns:
        JSON list of problem record summaries.
    """
    try:
        result = await _client.list_problems(state=state)
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "problems": [format_problem(p) for p in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 6. list_assets ─────────────────────


@mcp.tool()
async def list_assets(
    keyword: str | None = None,
) -> str:
    """List and search hardware assets.

    Args:
        keyword: Search assets by name.

    Returns:
        JSON list of asset summaries.
    """
    try:
        result = await _client.list_assets(keyword=keyword)
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "assets": [format_asset(a) for a in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 7. get_asset_incidents ─────────────────────


@mcp.tool()
async def get_asset_incidents(asset_id: int) -> str:
    """Get incidents linked to a specific hardware asset.

    Args:
        asset_id: The numeric ID of the hardware asset.

    Returns:
        JSON list of incident summaries linked to the asset.
    """
    try:
        result = await _client.get_asset_incidents(asset_id)
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "incidents": [format_incident_summary(i) for i in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 8. search_knowledge_base ─────────────────────


@mcp.tool()
async def search_knowledge_base(
    keyword: str | None = None,
) -> str:
    """Search knowledge base / solutions articles.

    Args:
        keyword: Search term for KB article titles.

    Returns:
        JSON list of matching KB articles.
    """
    try:
        result = await _client.search_solutions(keyword=keyword)
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "articles": [format_solution(s) for s in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 9. list_categories ─────────────────────


@mcp.tool()
async def list_categories() -> str:
    """List all incident categories and subcategories.

    Returns:
        JSON list of categories with their subcategories.
    """
    try:
        result = await _client.list_categories()
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "categories": [format_category(c) for c in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 10. list_users ─────────────────────


@mcp.tool()
async def list_users(
    keyword: str | None = None,
    role: str | None = None,
) -> str:
    """Search users by name or role.

    Args:
        keyword: Search users by name.
        role: Filter by role name.

    Returns:
        JSON list of user summaries.
    """
    try:
        result = await _client.list_users(keyword=keyword, role=role)
        response = {
            "count": len(result.records),
            **_truncation_fields(result),
            "users": [format_user(u) for u in result.records],
        }
        return _json(response)
    except Exception as e:
        return _error(e)


# ───────────────────── 11. get_sla_performance ─────────────────────


@mcp.tool()
async def get_sla_performance(
    state: list[str] | None = None,
    priority: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> str:
    """Analyze SLA compliance based on incident due dates vs resolution times.

    Args:
        state: Filter by state(s)
        priority: Filter by priority(ies)
        created_after: ISO-8601 start date
        created_before: ISO-8601 end date

    Returns:
        JSON with SLA compliance percentage, met/breached/pending counts, and breakdown by priority.
    """
    try:
        result = await _client.list_incidents(
            state=state,
            priority=priority,
            created_after=created_after,
            created_before=created_before,
        )
        sla = compute_sla_performance(result.records)
        if result.is_truncated:
            sla["data_truncated"] = True
            sla["data_truncated_warning"] = (
                f"SLA analysis computed from {len(result.records)} of "
                f"{result.total_count or 'unknown'} total records."
            )
        return _json(sla)
    except Exception as e:
        return _error(e)


# ───────────────────── Entry point ─────────────────────


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
