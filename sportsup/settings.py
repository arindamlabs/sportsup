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

    # Telegram Bot API (the delivery channel)
    telegram_bot_token: str | None = Field(None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(None, alias="TELEGRAM_CHAT_ID")

    # Global override for delivery.dry_run; None means "use config value".
    dry_run_override: bool | None = Field(None, alias="SPORTSUP_DRY_RUN")

    # Admin dashboard (read-only). Password is REQUIRED to start the dashboard.
    dashboard_user: str = Field("admin", alias="DASHBOARD_USER")
    dashboard_password: str | None = Field(None, alias="DASHBOARD_PASSWORD")

    def configured_providers(self) -> dict[str, bool]:
        """Which integrations have credentials present (for the status banner)."""
        return {
            "football-data.org": bool(self.football_data_api_key),
            "api-football": bool(self.api_football_key),
            "telegram": bool(self.telegram_bot_token and self.telegram_chat_id),
        }
