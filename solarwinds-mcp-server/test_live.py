"""Quick smoke test against the live SolarWinds API."""

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

from solarwinds_mcp.client import SolarWindsClient, SolarWindsClientError
from solarwinds_mcp.config import SolarWindsConfig


async def main():
    config = SolarWindsConfig()
    client = SolarWindsClient(config)

    print(f"Region: {config.region}")
    print(f"Base URL: {config.base_url}")
    print(f"Token: {config.api_token[:8]}...{config.api_token[-4:]}")
    print()

    # Test 1: List categories (simple, no filters)
    print("=== Test 1: List Categories ===")
    try:
        cats = await client.list_categories()
        print(f"Found {len(cats)} categories")
        for c in cats[:5]:
            print(f"  - {c.get('name')}")
    except SolarWindsClientError as e:
        print(f"ERROR: {e}")
    print()

    # Test 2: Search incidents (no filters = recent incidents)
    print("=== Test 2: Recent Incidents ===")
    try:
        incidents = await client.list_incidents()
        print(f"Found {len(incidents)} incidents")
        for inc in incidents[:5]:
            print(f"  [{inc.get('priority')}] {inc.get('state')}: {inc.get('name')}")
    except SolarWindsClientError as e:
        print(f"ERROR: {e}")
    print()

    # Test 3: List users
    print("=== Test 3: List Users ===")
    try:
        users = await client.list_users()
        print(f"Found {len(users)} users")
        for u in users[:5]:
            print(f"  - {u.get('name')} ({u.get('email')})")
    except SolarWindsClientError as e:
        print(f"ERROR: {e}")

    await client.close()
    print("\nDone!")


asyncio.run(main())
