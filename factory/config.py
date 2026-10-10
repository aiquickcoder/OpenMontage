"""Factory settings, all from the environment (/opt/openmontage/.env on the server)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lib.paths import REPO_ROOT


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    repo: Path
    data_dir: Path
    releases_dir: Path
    claude_bin: str
    claude_proxy: str
    claude_max_turns: int
    run_timeout_s: int
    tg_token: str
    tg_chat: str
    tg_proxy: str
    release_base_url: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "factory.db"

    @property
    def projects_dir(self) -> Path:
        return self.repo / "projects"


def load() -> Settings:
    repo = Path(_env("FACTORY_REPO") or REPO_ROOT)
    data = Path(_env("FACTORY_DATA_DIR") or repo / "factory_data")
    return Settings(
        repo=repo,
        data_dir=data,
        releases_dir=Path(_env("FACTORY_RELEASES_DIR") or data / "releases"),
        claude_bin=_env("FACTORY_CLAUDE_BIN", "claude"),
        claude_proxy=_env("FACTORY_CLAUDE_PROXY"),
        claude_max_turns=int(_env("FACTORY_CLAUDE_MAX_TURNS", "400")),
        run_timeout_s=int(_env("FACTORY_RUN_TIMEOUT_S", str(3 * 3600))),
        tg_token=_env("TG_TOKEN"),
        tg_chat=_env("TG_CHAT"),
        tg_proxy=_env("TG_PROXY"),
        release_base_url=_env("FACTORY_RELEASE_BASE_URL", "https://studio.luvael.ru/releases"),
    )
