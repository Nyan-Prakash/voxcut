"""Runtime configuration.

Most user-facing settings (API keys, quality tiers) live in the DB per the spec
(never env files). This module only holds bootstrap-level config needed *before*
the DB exists: where the data dir is, the bind host/port, and the per-install
security token.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path


class Bootstrap:
    HOST = "127.0.0.1"  # NFR1 — never bind 0.0.0.0
    PORT = int(os.environ.get("VOXCUT_PORT", "8484"))

    def __init__(self) -> None:
        # Data dir: env override, else ~/VOXCUT (first-run wizard can relocate later).
        env_dir = os.environ.get("VOXCUT_DATA_DIR")
        self.data_dir = Path(env_dir).expanduser() if env_dir else Path.home() / "VOXCUT"
        self.projects_dir = self.data_dir / "projects"
        self.library_dir = self.data_dir / "library"
        self.db_path = self.data_dir / "voxcut.db"
        self._token_path = self.data_dir / ".session_token"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.projects_dir, self.library_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def session_token(self) -> str:
        """Random per-install token (§14) so other local processes can't hit the API.

        Persisted so a page refresh / relaunch reuses the same token.
        """
        if self._token_path.exists():
            return self._token_path.read_text().strip()
        token = secrets.token_urlsafe(24)
        self._token_path.write_text(token)
        return token

    def project_dir(self, project_id: str) -> Path:
        d = self.projects_dir / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def base_url(self) -> str:
        return f"http://{self.HOST}:{self.PORT}/?t={self.session_token}"


@lru_cache
def settings() -> Bootstrap:
    s = Bootstrap()
    s.ensure_dirs()
    return s
