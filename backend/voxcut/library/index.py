"""Asset Library cache index (spec §8.3): reuse, pruning, disk usage."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..config import settings
from ..models import Asset, utcnow


def find_asset(db: Session, provider: str, source_id: str) -> Asset | None:
    return db.exec(
        select(Asset).where(Asset.provider == provider,
                            Asset.source_id == source_id)
    ).first()


def record_asset(db: Session, meta: dict, queries: list[str]) -> Asset:
    """Insert or update an asset row from a provider's fetch() result."""
    existing = find_asset(db, meta["provider"], meta["source_id"])
    if existing:
        prev = set(json.loads(existing.queries or "[]"))
        existing.queries = json.dumps(sorted(prev | set(queries)))
        existing.last_used_at = utcnow()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    a = Asset(
        provider=meta["provider"], source_id=meta["source_id"],
        source_url=meta.get("source_url", ""), title=meta.get("title", ""),
        duration_s=meta.get("duration_s", 0.0), width=meta.get("width", 0),
        height=meta.get("height", 0), fps=meta.get("fps", 0.0),
        file_path=meta["file_path"], subs_path=meta.get("subs_path"),
        heatmap_path=meta.get("heatmap_path"),
        queries=json.dumps(sorted(set(queries))),
        size_bytes=meta.get("size_bytes", 0),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def touch(db: Session, asset_id: str) -> None:
    a = db.get(Asset, asset_id)
    if a:
        a.last_used_at = utcnow()
        db.add(a)
        db.commit()


def disk_usage_bytes(db: Session) -> int:
    return sum(a.size_bytes for a in db.exec(select(Asset)).all())


def prune(db: Session, *, older_than_days: int | None = None,
          max_gb: float | None = None) -> dict:
    """Remove unpinned assets: those unused for N days, then LRU until under cap."""
    import shutil
    removed = []
    now = datetime.now(timezone.utc)

    def _delete(a: Asset) -> None:
        folder = settings().library_dir / a.source_id
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(a.id)
        db.delete(a)

    if older_than_days is not None:
        cutoff = now - timedelta(days=older_than_days)
        for a in db.exec(select(Asset).where(Asset.pinned == False)).all():  # noqa: E712
            lu = a.last_used_at
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            if lu < cutoff:
                _delete(a)
    db.commit()

    if max_gb is not None:
        cap = max_gb * 1e9
        assets = db.exec(
            select(Asset).where(Asset.pinned == False)  # noqa: E712
            .order_by(Asset.last_used_at)
        ).all()
        total = disk_usage_bytes(db)
        for a in assets:
            if total <= cap:
                break
            total -= a.size_bytes
            _delete(a)
        db.commit()

    return {"removed": removed, "count": len(removed)}
