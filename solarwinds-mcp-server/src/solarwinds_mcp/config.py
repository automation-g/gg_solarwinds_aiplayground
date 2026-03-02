"""Configuration loading and validation for SolarWinds Service Desk MCP server."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SolarWindsConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOLARWINDS_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    api_token: str = Field(description="SolarWinds Service Desk API token (JWT)")
    region: str = Field(default="us", description="API region: 'us' or 'eu'")
    per_page: int = Field(default=100, ge=1, le=100, description="Results per page")
    max_pages: int = Field(default=50, ge=1, description="Safety limit for pagination")

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("us", "eu"):
            raise ValueError("region must be 'us' or 'eu'")
        return v

    @property
    def base_url(self) -> str:
        if self.region == "eu":
            return "https://apieu.samanage.com"
        return "https://api.samanage.com"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Samanage-Authorization": f"Bearer {self.api_token}",
            "Accept": "application/vnd.samanage.v2.1+json",
            "Content-Type": "application/json",
        }
