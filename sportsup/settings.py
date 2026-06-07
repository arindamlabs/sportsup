"""Secrets and environment-driven settings, loaded from `.env` / the environment.

Kept strictly separate from :mod:`sportsup.config` so that no secret ever lands
in a committed YAML file. Nothing here is required for the Phase 1 skeleton to boot.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Sports data providers (Phase 2)
    football_data_api_key: str | None = Field(None, alias="FOOTBALL_DATA_API_KEY")
    api_football_key: str | None = Field(None, alias="API_FOOTBALL_KEY")

    # WhatsApp Cloud API (Phase 4)
    whatsapp_access_token: str | None = Field(None, alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str | None = Field(None, alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_recipient: str | None = Field(None, alias="WHATSAPP_RECIPIENT")

    # Global override for delivery.dry_run; None means "use config value".
    dry_run_override: bool | None = Field(None, alias="SPORTSUP_DRY_RUN")

    def configured_providers(self) -> dict[str, bool]:
        """Which integrations have credentials present (for the status banner)."""
        return {
            "football-data.org": bool(self.football_data_api_key),
            "api-football": bool(self.api_football_key),
            "whatsapp-cloud": bool(
                self.whatsapp_access_token and self.whatsapp_phone_number_id
            ),
        }
