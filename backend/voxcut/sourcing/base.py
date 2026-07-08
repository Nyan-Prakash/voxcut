"""SourceProvider interface (spec §7.5, §8.1).

YouTube is the first implementation; Giphy/stock/AI-gen slot in later without
touching the planner or editor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Filters:
    max_duration_s: float = 900.0     # skip 1h+ streams/loops by default
    min_duration_s: float = 5.0
    avoid: list[str] = field(default_factory=list)
    reaction_intent: bool = False     # tweak ranking for reaction-type beats


@dataclass
class Candidate:
    provider: str
    source_id: str
    url: str
    title: str
    duration_s: float
    view_count: int = 0
    channel: str = ""
    channel_verified: bool = False
    live: bool = False
    thumbnail: str = ""
    score: float = 0.0


class SourceProvider(Protocol):
    name: str

    def search(self, query: str, n: int, filters: Filters) -> list[Candidate]: ...
    def fetch(self, candidate: Candidate, dest: Path) -> dict: ...
    def fetch_url(self, url: str, dest: Path) -> dict: ...
