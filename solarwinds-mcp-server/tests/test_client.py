"""Tests for the SolarWinds HTTP client and formatting helpers."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from httpx import Response

from solarwinds_mcp.client import SolarWindsClient, SolarWindsClientError
from solarwinds_mcp.config import SolarWindsConfig
from solarwinds_mcp.formatting import (
    compute_incident_statistics,
    compute_sla_performance,
    format_incident_summary,
)

from .conftest import make_incident, make_paginated_response


# ═══════════════════ Config tests ═══════════════════


class TestConfig:
    def test_us_base_url(self):
        cfg = SolarWindsConfig(api_token="tok", region="us")
        assert cfg.base_url == "https://api.samanage.com"

    def test_eu_base_url(self):
        cfg = SolarWindsConfig(api_token="tok", region="eu")
        assert cfg.base_url == "https://apieu.samanage.com"

    def test_headers_contain_token(self):
        cfg = SolarWindsConfig(api_token="my-jwt")
        assert cfg.headers["X-Samanage-Authorization"] == "Bearer my-jwt"
        assert "samanage" in cfg.headers["Accept"]

    def test_invalid_region(self):
        with pytest.raises(ValueError, match="region must be"):
            SolarWindsConfig(api_token="tok", region="asia")


# ═══════════════════ Client tests ═══════════════════


class TestClient:
    @pytest.mark.asyncio
    async def test_get_single(self, client, mock_api):
        mock_api.get("/incidents/42.json").mock(
            return_value=Response(200, json={"id": 42, "name": "test"})
        )
        result = await client.get_single("/incidents/42.json")
        assert result["id"] == 42

    @pytest.mark.asyncio
    async def test_get_paginated_single_page(self, client, mock_api):
        items = [make_incident(id=i) for i in range(3)]
        mock_api.get("/incidents.json").mock(
            return_value=make_paginated_response(items, page=1, total_pages=1)
        )
        result = await client.get_paginated("/incidents.json")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_paginated_multi_page(self, client, mock_api):
        page1 = [make_incident(id=1), make_incident(id=2)]
        page2 = [make_incident(id=3)]

        route = mock_api.get("/incidents.json")
        route.side_effect = [
            make_paginated_response(page1, page=1, total_pages=2, total_count=3),
            make_paginated_response(page2, page=2, total_pages=2, total_count=3),
        ]

        result = await client.get_paginated("/incidents.json")
        assert len(result) == 3
        assert [r["id"] for r in result] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_get_paginated_respects_max_pages(self, client, mock_api):
        # max_pages=2 in fixture, API says 5 pages
        page1 = [make_incident(id=1)]
        page2 = [make_incident(id=2)]

        route = mock_api.get("/incidents.json")
        route.side_effect = [
            make_paginated_response(page1, page=1, total_pages=5, total_count=5),
            make_paginated_response(page2, page=2, total_pages=5, total_count=5),
        ]

        result = await client.get_paginated("/incidents.json")
        assert len(result) == 2  # stopped at max_pages=2

    @pytest.mark.asyncio
    async def test_api_error_raises(self, client, mock_api):
        mock_api.get("/incidents.json").mock(
            return_value=Response(401, text="Unauthorized")
        )
        with pytest.raises(SolarWindsClientError, match="401"):
            await client.get_paginated("/incidents.json")

    @pytest.mark.asyncio
    async def test_list_incidents_passes_filters(self, client, mock_api):
        route = mock_api.get("/incidents.json").mock(
            return_value=make_paginated_response([make_incident()])
        )
        await client.list_incidents(state=["New"], priority=["Critical"], keyword="vpn")
        request = route.calls[0].request
        url = str(request.url)
        assert "state%5B%5D=New" in url or "state[]=New" in url
        assert "priority%5B%5D=Critical" in url or "priority[]=Critical" in url

    @pytest.mark.asyncio
    async def test_get_incident_uses_long_layout(self, client, mock_api):
        route = mock_api.get(url__regex=r"/incidents/99\.json.*").mock(
            return_value=Response(200, json=make_incident(id=99))
        )
        result = await client.get_incident(99)
        assert result["id"] == 99
        request_url = str(route.calls[0].request.url)
        assert "layout=long" in request_url


# ═══════════════════ Formatting tests ═══════════════════


class TestFormatting:
    def test_incident_summary_extracts_fields(self):
        inc = make_incident(id=5, name="Printer jam", state="Assigned", priority="High")
        result = format_incident_summary(inc)
        assert result["id"] == 5
        assert result["name"] == "Printer jam"
        assert result["state"] == "Assigned"
        assert result["priority"] == "High"
        assert result["category"] == "Hardware"
        assert result["assignee"] == "John Doe"

    def test_incident_summary_handles_nulls(self):
        inc = {"id": 1, "name": "test", "state": "New", "priority": None}
        result = format_incident_summary(inc)
        assert result["priority"] is None
        assert result["category"] is None
        assert result["assignee"] is None


# ═══════════════════ Statistics tests ═══════════════════


class TestStatistics:
    def test_empty_incidents(self):
        stats = compute_incident_statistics([])
        assert stats["total"] == 0

    def test_counts_by_state_and_priority(self):
        incidents = [
            make_incident(state="New", priority="High"),
            make_incident(state="New", priority="Low"),
            make_incident(state="Assigned", priority="High"),
        ]
        stats = compute_incident_statistics(incidents)
        assert stats["total"] == 3
        assert stats["by_state"]["New"] == 2
        assert stats["by_state"]["Assigned"] == 1
        assert stats["by_priority"]["High"] == 2

    def test_resolution_time_computed(self):
        incidents = [
            make_incident(
                created_at="2024-06-01T10:00:00+00:00",
                resolved_at="2024-06-01T14:00:00+00:00",  # 4 hours
            ),
            make_incident(
                created_at="2024-06-01T10:00:00+00:00",
                resolved_at="2024-06-01T16:00:00+00:00",  # 6 hours
            ),
        ]
        stats = compute_incident_statistics(incidents)
        assert stats["avg_resolution_hours"] == 5.0
        assert stats["resolved_count"] == 2

    def test_overdue_detection(self):
        incidents = [
            make_incident(
                due_at="2024-06-01T17:00:00+00:00",
                resolved_at="2024-06-02T10:00:00+00:00",  # resolved after due
            ),
        ]
        stats = compute_incident_statistics(incidents)
        assert stats["overdue_count"] == 1


class TestSLAPerformance:
    def test_no_sla_incidents(self):
        incidents = [make_incident(due_at=None)]
        result = compute_sla_performance(incidents)
        assert result["total_with_sla"] == 0

    def test_sla_met_and_breached(self):
        incidents = [
            make_incident(
                priority="High",
                due_at="2024-06-05T17:00:00+00:00",
                resolved_at="2024-06-04T10:00:00+00:00",  # met
            ),
            make_incident(
                priority="High",
                due_at="2024-06-05T17:00:00+00:00",
                resolved_at="2024-06-06T10:00:00+00:00",  # breached
            ),
        ]
        result = compute_sla_performance(incidents)
        assert result["met"] == 1
        assert result["breached"] == 1
        assert result["compliance_percent"] == 50.0
        assert result["by_priority"]["High"]["met"] == 1
        assert result["by_priority"]["High"]["breached"] == 1
