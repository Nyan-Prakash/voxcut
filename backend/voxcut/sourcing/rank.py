"""Pre-download candidate ranking (spec §8.2).

score = 2.0*title_sim + 0.8*duration_fit + 0.4*log10(views)/8
        + 0.3*channel_signal - 1.5*livestream/10h - 2.0*avoid_hit
title_sim uses a token-set ratio (dependency-light); the semantic embedder used
for moment selection (M5) can be swapped in here later.
"""
from __future__ import annotations

import math

from rapidfuzz import fuzz

from .base import Candidate, Filters


def _title_sim(query: str, title: str) -> float:
    return fuzz.token_set_ratio(query.lower(), title.lower()) / 100.0


def _duration_fit(dur: float) -> float:
    """Ideal 20s–8min (piecewise): long enough to contain the moment, short
    enough to download/scan cheaply."""
    if dur <= 0:
        return 0.0
    if dur < 20:
        return dur / 20.0
    if dur <= 480:
        return 1.0
    if dur <= 900:
        return 1.0 - (dur - 480) / (900 - 480) * 0.6
    return 0.2


def _channel_signal(c: Candidate, reaction: bool) -> float:
    name = c.channel.lower()
    clip_channel = any(k in name for k in ("clip", "compilation", "moments",
                                           "best of", "highlights"))
    sig = 0.0
    if c.channel_verified:
        sig += 0.4
    if reaction and clip_channel:
        sig += 1.0
    return min(1.0, sig)


def score_candidate(query: str, c: Candidate, filters: Filters) -> float:
    title_sim = _title_sim(query, c.title)
    dur_fit = _duration_fit(c.duration_s)
    pop = math.log10(c.view_count + 1) / 8.0
    chan = _channel_signal(c, filters.reaction_intent)

    penalty = 0.0
    if c.live or c.duration_s > 36000:  # 10h
        penalty += 1.5
    hay = f"{c.title} {c.channel}".lower()
    if any(a.lower() in hay for a in filters.avoid):
        penalty += 2.0

    return round(2.0 * title_sim + 0.8 * dur_fit + 0.4 * pop
                 + 0.3 * chan - penalty, 4)


def rank(query: str, candidates: list[Candidate], filters: Filters) -> list[Candidate]:
    for c in candidates:
        c.score = score_candidate(query, c, filters)
    return sorted(candidates, key=lambda c: c.score, reverse=True)
