"""Studio settings: the factory's settings plus web-only ones, all from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from factory import config as factory_config


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class StudioSettings:
    factory: factory_config.Settings
    admin_user: str
    admin_password_hash: str
    secret: str
    cookie_secure: bool
    session_days: int
    web_dist: Path
    farm_agent_token: str

    @property
    def db_path(self) -> Path:
        return self.factory.data_dir / "studio.db"

    @property
    def characters_dir(self) -> Path:
        return self.factory.data_dir / "characters"

    @property
    def uploads_dir(self) -> Path:
        return self.factory.data_dir / "studio_uploads"


def load() -> StudioSettings:
    fs = factory_config.load()
    return StudioSettings(
        factory=fs,
        admin_user=_env("STUDIO_ADMIN_USER", "owner"),
        admin_password_hash=_env("STUDIO_ADMIN_PASSWORD_HASH"),
        secret=_env("STUDIO_SECRET"),
        cookie_secure=_env("STUDIO_COOKIE_SECURE", "1") != "0",
        session_days=int(_env("STUDIO_SESSION_DAYS", "14")),
        web_dist=Path(_env("STUDIO_WEB_DIST") or fs.repo / "studio" / "web" / "dist"),
        farm_agent_token=_env("FARM_AGENT_TOKEN"),
    )
