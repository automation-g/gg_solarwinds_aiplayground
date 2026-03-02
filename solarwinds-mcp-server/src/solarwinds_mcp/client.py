"""Async HTTP client for SolarWinds Service Desk API (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import SolarWindsConfig


class SolarWindsClientError(Exception):
    """Raised when the SolarWinds API returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


@dataclass
class PaginatedResult:
    """Result from a paginated API call, including truncation metadata."""

    records: list[dict[str, Any]]
    total_pages: int | None = None
    total_count: int | None = None
    is_truncated: bool = False


class SolarWindsClient:
    """Read-only async client for the SolarWinds Service Desk REST API."""

    def __init__(self, config: SolarWindsConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._user_id_cache: dict[str, int | None] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ----- low-level helpers -----

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform a single GET request and return the response."""
        client = await self._get_client()
        resp = await client.get(path, params=params)
        if resp.status_code >= 400:
            raise SolarWindsClientError(resp.status_code, resp.text[:500])
        return resp

    async def get_single(self, path: str) -> dict[str, Any]:
        """GET a single resource (no pagination)."""
        resp = await self._get(path)
        return resp.json()

    async def get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> PaginatedResult:
        """GET a paginated list endpoint, collecting all pages up to the safety limit.

        Returns a PaginatedResult with records and metadata about truncation.
        """
        if params is None:
            params = {}
        params.setdefault("per_page", self.config.per_page)
        if max_pages is None:
            max_pages = self.config.max_pages

        all_records: list[dict[str, Any]] = []
        page = 1
        total_pages: int | None = None
        total_count: int | None = None

        while page <= max_pages:
            params["page"] = page
            resp = await self._get(path, params=params)
            data = resp.json()

            # Capture pagination headers
            total_pages = int(resp.headers.get("X-Total-Pages", "1"))
            total_count_hdr = resp.headers.get("X-Total-Count")
            if total_count_hdr is not None:
                total_count = int(total_count_hdr)

            # The API returns a JSON array at the top level for list endpoints
            if isinstance(data, list):
                if not data:
                    break
                all_records.extend(data)
            else:
                # Some endpoints wrap in a key — just return as-is
                return PaginatedResult(
                    records=[data],
                    total_pages=1,
                    total_count=1,
                    is_truncated=False,
                )

            if page >= total_pages:
                break
            page += 1

        is_truncated = total_pages is not None and page < total_pages

        return PaginatedResult(
            records=all_records,
            total_pages=total_pages,
            total_count=total_count,
            is_truncated=is_truncated,
        )

    # ----- assignee group ID resolution -----

    async def _resolve_assignee_group_id(self, name: str) -> int | None:
        """Look up a user by name and return their personal group ID for assignee filtering.

        The Samanage API's assigned_to[] filter requires the user's personal
        group ID (from group_ids), not their user ID.
        """
        cache_key = name.lower().strip()
        if cache_key in self._user_id_cache:
            return self._user_id_cache[cache_key]

        result = await self.list_users(keyword=name)

        # Find the best matching user (exact match first, then first result)
        matched_user: dict[str, Any] | None = None
        for user in result.records:
            if (user.get("name") or "").lower() == cache_key:
                matched_user = user
                break
        if matched_user is None and result.records:
            matched_user = result.records[0]

        if matched_user is None:
            self._user_id_cache[cache_key] = None
            return None

        # Fetch full user details to get group_ids (personal group)
        user_id = matched_user.get("id")
        full_user = await self.get_single(f"/users/{user_id}.json")
        group_ids = full_user.get("group_ids") or []
        group_id = group_ids[0] if group_ids else None

        self._user_id_cache[cache_key] = group_id
        return group_id

    # ----- resource methods (all read-only) -----

    async def list_incidents(
        self,
        *,
        state: list[str] | None = None,
        priority: list[str] | None = None,
        category: str | None = None,
        assignee: str | None = None,
        keyword: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "DESC",
    ) -> PaginatedResult:
        params: dict[str, Any] = {
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        if state:
            params["state[]"] = state
        if priority:
            params["priority[]"] = priority
        if category:
            params["category_name"] = category
        if assignee:
            group_id = await self._resolve_assignee_group_id(assignee)
            if group_id is not None:
                params["assigned_to[]"] = group_id
            else:
                # No matching user found — return empty result
                return PaginatedResult(
                    records=[], total_pages=0, total_count=0, is_truncated=False,
                )
        if keyword:
            params["name"] = keyword
        if created_after or created_before:
            params["created[]"] = "Select Date Range"
            if created_after:
                params["created_custom_gte"] = created_after
            if created_before:
                params["created_custom_lte"] = created_before
        return await self.get_paginated("/incidents.json", params)

    async def get_incident(self, incident_id: int) -> dict[str, Any]:
        return await self.get_single(
            f"/incidents/{incident_id}.json?layout=long&audit_archives=true"
        )

    async def list_changes(
        self,
        *,
        state: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> PaginatedResult:
        params: dict[str, Any] = {"sort_by": "created_at", "sort_order": "DESC"}
        if state:
            params["state[]"] = state
        if created_after or created_before:
            params["created[]"] = "Select Date Range"
            if created_after:
                params["created_custom_gte"] = created_after
            if created_before:
                params["created_custom_lte"] = created_before
        return await self.get_paginated("/changes.json", params)

    async def list_problems(
        self,
        *,
        state: list[str] | None = None,
    ) -> PaginatedResult:
        params: dict[str, Any] = {"sort_by": "created_at", "sort_order": "DESC"}
        if state:
            params["state[]"] = state
        return await self.get_paginated("/problems.json", params)

    async def list_assets(
        self,
        *,
        keyword: str | None = None,
    ) -> PaginatedResult:
        params: dict[str, Any] = {}
        if keyword:
            params["name"] = keyword
        return await self.get_paginated("/hardwares.json", params)

    async def get_asset_incidents(self, asset_id: int) -> PaginatedResult:
        return await self.get_paginated(f"/hardwares/{asset_id}/incidents.json")

    async def search_solutions(
        self,
        *,
        keyword: str | None = None,
    ) -> PaginatedResult:
        params: dict[str, Any] = {}
        if keyword:
            params["name"] = keyword
        return await self.get_paginated("/solutions.json", params)

    async def list_categories(self) -> PaginatedResult:
        return await self.get_paginated("/categories.json")

    async def list_users(
        self,
        *,
        keyword: str | None = None,
        role: str | None = None,
    ) -> PaginatedResult:
        params: dict[str, Any] = {}
        if keyword:
            params["name"] = keyword
        if role:
            params["role"] = role
        return await self.get_paginated("/users.json", params)
