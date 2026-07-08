"""Transcript read + correction (spec §5.4, §11.3).

Corrections write `corrected_text` (original preserved). Editing a word inside a
beat's span flags the beat stale — that re-derivation lands with beats in M2.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Word

router = APIRouter(prefix="/api/projects", tags=["transcript"])


class WordOut(BaseModel):
    idx: int
    text: str
    start_s: float
    end_s: float
    confidence: float
    corrected_text: str | None


@router.get("/{project_id}/transcript")
def get_transcript(project_id: str, db: Session = Depends(get_session)) -> dict:
    rows = db.exec(
        select(Word).where(Word.project_id == project_id).order_by(Word.idx)
    ).all()
    words = [WordOut(idx=w.idx, text=w.text, start_s=w.start_s, end_s=w.end_s,
                     confidence=w.confidence, corrected_text=w.corrected_text)
             for w in rows]
    return {"count": len(words), "words": [w.model_dump() for w in words]}


class Correction(BaseModel):
    idx: int
    corrected_text: str


class CorrectionBatch(BaseModel):
    corrections: list[Correction]


@router.patch("/{project_id}/transcript")
def correct_transcript(project_id: str, body: CorrectionBatch,
                       db: Session = Depends(get_session)) -> dict:
    by_idx = {c.idx: c.corrected_text for c in body.corrections}
    rows = db.exec(
        select(Word).where(Word.project_id == project_id, Word.idx.in_(list(by_idx)))
    ).all()
    if not rows:
        raise HTTPException(404, "no matching words")
    for w in rows:
        w.corrected_text = by_idx[w.idx]
        db.add(w)
    db.commit()
    return {"updated": len(rows)}
