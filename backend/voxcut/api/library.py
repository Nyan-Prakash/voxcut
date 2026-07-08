"""Asset Library API (spec §8.3, §11.3)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..library.index import disk_usage_bytes, prune
from ..models import Asset, new_id
from ..sourcing.local import LocalProvider

router = APIRouter(prefix="/api/library", tags=["library"])


def _asset_out(a: Asset) -> dict:
    return {
        "id": a.id, "provider": a.provider, "source_id": a.source_id,
        "title": a.title, "duration_s": a.duration_s,
        "width": a.width, "height": a.height,
        "queries": json.loads(a.queries or "[]"),
        "size_bytes": a.size_bytes, "pinned": a.pinned,
        "has_subs": bool(a.subs_path), "has_heatmap": bool(a.heatmap_path),
        "last_used_at": a.last_used_at.isoformat(),
    }


@router.get("")
def list_assets(q: str | None = None, db: Session = Depends(get_session)) -> dict:
    rows = db.exec(select(Asset).order_by(Asset.last_used_at.desc())).all()
    if q:
        ql = q.lower()
        rows = [a for a in rows
                if ql in a.title.lower() or ql in (a.queries or "").lower()]
    return {"assets": [_asset_out(a) for a in rows],
            "disk_usage_bytes": disk_usage_bytes(db)}


@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 db: Session = Depends(get_session)) -> dict:
    source_id = new_id("local").replace("local_", "loc")
    dest = settings().library_dir / source_id
    tmp = dest.with_suffix(".tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    raw = tmp / (file.filename or "upload.mp4")
    with raw.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    meta = LocalProvider().ingest(raw, dest, source_id)
    shutil.rmtree(tmp, ignore_errors=True)

    from ..library.index import record_asset
    tags = [Path(file.filename or "").stem] if file.filename else []
    asset = record_asset(db, meta, tags)
    return _asset_out(asset)


class PinBody(BaseModel):
    pinned: bool


@router.post("/{asset_id}/pin")
def pin(asset_id: str, body: PinBody, db: Session = Depends(get_session)) -> dict:
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    a.pinned = body.pinned
    db.add(a)
    db.commit()
    return _asset_out(a)


class PruneBody(BaseModel):
    older_than_days: int | None = None
    max_gb: float | None = None


@router.post("/prune")
def prune_library(body: PruneBody, db: Session = Depends(get_session)) -> dict:
    return prune(db, older_than_days=body.older_than_days, max_gb=body.max_gb)
