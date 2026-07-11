"""SQLModel tables (spec §4). Large artifacts live on disk; rows store paths."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel
from ulid import ULID


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("pr"), primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    voiceover_path: str | None = None
    duration_s: float = 0.0
    context_brief: str = "{}"  # JSON blob, schema in §4.2
    settings: str = "{}"       # JSON: aspect, cut_density, resolution
    edl_version: int = 0
    status: str = "draft"      # draft | generating | ready | error


class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("jb"), primary_key=True)
    project_id: str | None = Field(default=None, index=True)
    kind: str                                   # generate | regenerate_span | export | download | demo
    state: str = "queued"                       # queued | running | done | failed | cancelled
    steps: str = "[]"                           # JSON: [{name, state, progress, message}]
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class Asset(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("as"), primary_key=True)
    provider: str                               # youtube | local | ...
    source_id: str = Field(index=True)
    source_url: str = ""
    title: str = ""
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    file_path: str = ""
    subs_path: str | None = None
    heatmap_path: str | None = None
    scenes_path: str | None = None
    queries: str = "[]"                         # JSON list of queries that led here
    downloaded_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)
    size_bytes: int = 0
    pinned: bool = False


class Word(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True)
    idx: int
    text: str
    start_s: float
    end_s: float
    confidence: float = 1.0
    corrected_text: str | None = None


class Setting(SQLModel, table=True):
    """Key/value settings store (§13) — never env files."""
    key: str = Field(primary_key=True)
    value: str = ""
