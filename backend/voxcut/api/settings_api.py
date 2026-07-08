"""Settings store (spec §13) — persisted in SQLite, never env files."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Sensitive keys are write-only over the API (returned as a boolean "is set").
SECRET_KEYS = {"claude_api_key"}
DEFAULTS = {
    "transcription_quality": "balanced",   # fast | balanced | best
    "download_cap_gb": "50",
    "export_resolution": "1080p",
    "web_search_assist": "false",
    "precision_alignment": "false",
}


def _get(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


@router.get("")
def get_settings(db: Session = Depends(get_session)) -> dict:
    from sqlmodel import select
    out = dict(DEFAULTS)
    for row in db.exec(select(Setting)).all():
        if row.key in SECRET_KEYS:
            continue
        out[row.key] = row.value
    for k in SECRET_KEYS:
        out[f"{k}_set"] = bool(_get(db, k))
    return out


class SettingsPut(BaseModel):
    values: dict[str, str]


@router.put("")
def put_settings(body: SettingsPut, db: Session = Depends(get_session)) -> dict:
    for key, value in body.values.items():
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            row = Setting(key=key, value=value)
        db.add(row)
    db.commit()
    return {"ok": True}


class TestKeyBody(BaseModel):
    claude_api_key: str | None = None


@router.post("/test_key")
async def test_key(body: TestKeyBody, db: Session = Depends(get_session)) -> dict:
    """Validate the Claude API key with a live, minimal call (§13)."""
    key = body.claude_api_key or _get(db, "claude_api_key")
    if not key:
        return {"ok": False, "error": "No API key provided."}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 8,
                      "messages": [{"role": "user", "content": "ping"}]},
            )
        if r.status_code == 200:
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
