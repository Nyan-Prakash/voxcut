"""System health, downloader canary + self-update (spec §8.2 NFR6, §18)."""
from __future__ import annotations

import shutil
import subprocess
import sys

from fastapi import APIRouter

from ..brain.client import is_available
from ..config import settings
from ..db import session_scope
from ..library.index import disk_usage_bytes

router = APIRouter(prefix="/api/system", tags=["system"])


def _ytdlp_version() -> str | None:
    exe = shutil.which("yt-dlp")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


@router.get("")
def system() -> dict:
    with session_scope() as db:
        usage = disk_usage_bytes(db)
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "yt_dlp_version": _ytdlp_version(),
        "brain_ready": is_available(),
        "data_dir": str(settings().data_dir),
        "library_bytes": usage,
    }


@router.post("/canary")
def canary() -> dict:
    """Health check: a trivial yt-dlp search (§18 downloader canary)."""
    exe = shutil.which("yt-dlp")
    if not exe:
        return {"ok": False, "error": "yt-dlp not installed"}
    try:
        out = subprocess.run([exe, "ytsearch1:test", "--flat-playlist", "-J",
                              "--no-warnings"],
                             capture_output=True, text=True, timeout=60)
        ok = out.returncode == 0 and out.stdout.strip().startswith("{")
        return {"ok": ok, "error": None if ok else out.stderr[-200:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@router.post("/update_ytdlp")
def update_ytdlp() -> dict:
    """One-click updater (NFR6)."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
            capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            return {"ok": False, "error": out.stderr[-300:]}
        return {"ok": True, "version": _ytdlp_version()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
