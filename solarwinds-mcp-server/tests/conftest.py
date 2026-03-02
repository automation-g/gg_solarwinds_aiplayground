"""Shared fixtures for SolarWinds MCP server tests."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from solarwinds_mcp.client import SolarWindsClient
from solarwinds_mcp.config import SolarWindsConfig


@pytest.fixture
def config() -> SolarWindsConfig:
    return SolarWindsConfig(
        api_token="test-token-123",
        region="us",
        per_page=25,
        max_pages=2,
    )


@pytest.fixture
def client(config: SolarWindsConfig) -> SolarWindsClient:
    return SolarWindsClient(config)


@pytest.fixture
def mock_api():
    """Activate respx mock for the SolarWinds API base URL."""
    with respx.mock(base_url="https://api.samanage.com", assert_all_called=False) as rsps:
        yield rsps


# ---- Sample data factories ----


def make_incident(
    id: int = 1,
    number: int = 1001,
    name: str = "Test incident",
    state: str = "New",
    priority: str = "Medium",
    **overrides,
) -> dict:
    inc = {
        "id": id,
        "number": number,
        "name": name,
        "state": state,
        "priority": priority,
        "category": {"name": "Hardware"},
        "assignee": {"name": "John Doe"},
        "requester": {"name": "Jane Smith"},
        "created_at": "2024-06-01T10:00:00Z",
        "updated_at": "2024-06-02T12:00:00Z",
        "due_at": "2024-06-05T17:00:00Z",
        "description": "Something is broken",
        "resolution": None,
        "resolved_at": None,
        "site": {"name": "HQ"},
        "department": {"name": "IT"},
        "comments": [],
        "assets": [],
        "audit_archives": [],
        "custom_fields_values": [],
    }
    inc.update(overrides)
    return inc


def make_paginated_response(
    items: list[dict],
    page: int = 1,
    total_pages: int = 1,
    total_count: int | None = None,
) -> Response:
    if total_count is None:
        total_count = len(items)
    return Response(
        200,
        json=items,
        headers={
            "X-Total-Count": str(total_count),
            "X-Total-Pages": str(total_pages),
            "X-Current-Page": str(page),
        },
    )
