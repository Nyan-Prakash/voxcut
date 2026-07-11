"""Beat segmentation engine (spec §6): LLM semantics + deterministic timing.

The LLM decides WHERE meaning shifts (on word indices, never timestamps); code
decides exactly WHEN the cut happens (silence > word-gap > word end). Falls back
to a pause/sentence heuristic when no LLM key is configured.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ulid import ULID

from .client import BrainError, is_available, structured
from .prompts import segmentation_prompts
from .schemas import SEGMENTATION_SCHEMA

# density → (target beat len s, min words, max words) — spec §6.2 table
DENSITY = {
    "chill":       (5.5, 8, 24),
    "normal":      (3.5, 5, 16),
    "hyperactive": (2.2, 3, 10),
}
WINDOW_S = 90.0
OVERLAP_S = 15.0
# Quick-cut beats (genuine lists / escalating rants) may run short; everything
# else holds at least ~1s on screen (operator-confirmed 2026-07-14).
MIN_BEAT_S = 1.0
PROTECTED_MIN_S = 0.2   # operator chose cut-per-item even for very fast lists
PROTECTED_RHYTHMS = {"list_item", "escalation"}
MAX_BEAT_S = 12.0
SILENCE_CUT = 0.12
GAP_CUT = 0.06


@dataclass
class W:
    idx: int
    text: str
    start_s: float
    end_s: float


@dataclass
class RawBeat:
    start_word: int
    end_word: int
    gist: str = ""
    tone: str = "neutral"
    emphasis: float = 0.4
    concrete_entities: list[str] = None  # type: ignore
    visual_affinity: str = "literal"
    rhythm: str = "flow"

    def __post_init__(self):
        if self.concrete_entities is None:
            self.concrete_entities = []


def segment(words: list[W], silences: list[tuple[float, float]],
            context: str, density: str, duration: float,
            use_llm: bool | None = None) -> list[dict]:
    if not words:
        return []
    if use_llm is None:
        use_llm = is_available()
    target_len, min_w, max_w = DENSITY.get(density, DENSITY["normal"])

    if use_llm:
        try:
            raw = _llm_segment(words, context, min_w, max_w)
        except BrainError:
            raw = _heuristic_segment(words, silences, min_w, max_w)
    else:
        raw = _heuristic_segment(words, silences, min_w, max_w)

    raw = _validate(raw, words)
    raw = _explode_lists(raw, words)
    raw = _density_fit(raw, words, target_len)
    return _finalize(raw, words, silences, duration)


# ---------------------------------------------------------------- LLM path
def _window_indices(words: list[W]) -> list[tuple[int, int]]:
    """Return (start_idx, end_idx exclusive) word ranges for 90s/15s windows."""
    if not words:
        return []
    ranges: list[tuple[int, int]] = []
    n = len(words)
    i = 0
    while i < n:
        t0 = words[i].start_s
        j = i
        while j < n and words[j].start_s < t0 + WINDOW_S:
            j += 1
        ranges.append((i, j))
        if j >= n:
            break
        # step back for overlap
        t_next = words[j - 1].start_s - OVERLAP_S
        k = i
        while k < j and words[k].start_s < t_next:
            k += 1
        i = max(k, i + 1)
    return ranges


def _llm_segment(words: list[W], context: str, min_w: int, max_w: int) -> list[RawBeat]:
    all_beats: list[RawBeat] = []
    for (s, e) in _window_indices(words):
        chunk = words[s:e]
        words_block = "\n".join(f"{w.idx}:{w.text.strip()}" for w in chunk)
        prev_ctx = ""
        if all_beats:
            tail = all_beats[-2:]
            prev_ctx = "; ".join(
                f"[words {b.start_word}-{b.end_word}] {b.gist}" for b in tail)
        system, user = segmentation_prompts(min_w, max_w, context, words_block, prev_ctx)
        out = structured(system, user, SEGMENTATION_SCHEMA,
                         schema_name="segmentation", temperature=0.3)
        window_beats = [RawBeat(**b) for b in out.get("beats", [])]
        all_beats = _stitch(all_beats, window_beats)
    return all_beats


def _stitch(existing: list[RawBeat], new: list[RawBeat]) -> list[RawBeat]:
    """Merge overlapping-window segmentations: keep existing beats up to the last
    agreed boundary, take new beats after that (they had more right-context)."""
    if not existing:
        return new
    last_end = existing[-1].end_word
    # Drop new beats fully inside already-covered territory; trim a straddler.
    tail: list[RawBeat] = []
    for b in new:
        if b.end_word <= last_end:
            continue
        if b.start_word <= last_end:
            b.start_word = last_end + 1
            if b.start_word > b.end_word:
                continue
        tail.append(b)
    return existing + tail


# ---------------------------------------------------------------- heuristic path
_SENT_END = re.compile(r"[.!?]+[\"')\]]?\s*$")


def _heuristic_segment(words: list[W], silences: list[tuple[float, float]],
                       min_w: int, max_w: int) -> list[RawBeat]:
    sil_starts = [s for (s, _e) in silences if (_e - s) >= 0.30]

    def big_pause_after(w: W) -> bool:
        return any(abs(s - w.end_s) < 0.15 or (w.end_s <= s) and s - w.end_s < 0.4
                   for s in sil_starts)

    beats: list[RawBeat] = []
    start = 0
    for i, w in enumerate(words):
        count = i - start + 1
        sentence_end = bool(_SENT_END.search(w.text))
        boundary = (
            (sentence_end and count >= min_w)
            or (big_pause_after(w) and count >= min_w)
            or count >= max_w
            or i == len(words) - 1
        )
        if boundary:
            text = " ".join(x.text.strip() for x in words[start:i + 1])
            emphasis = 0.7 if text.rstrip().endswith(("!", "?")) else (
                0.5 if text.rstrip().endswith(".") else 0.35)
            beats.append(RawBeat(start_word=words[start].idx, end_word=w.idx,
                                 gist=text[:120], emphasis=emphasis))
            start = i + 1
    return beats


# ---------------------------------------------------------------- validation
def _validate(beats: list[RawBeat], words: list[W]) -> list[RawBeat]:
    if not beats:
        return beats
    idx_min, idx_max = words[0].idx, words[-1].idx
    beats = sorted(beats, key=lambda b: b.start_word)

    # Clamp + repair coverage: force contiguous, monotonic, full coverage.
    fixed: list[RawBeat] = []
    cursor = idx_min
    for b in beats:
        b.start_word = cursor
        b.end_word = max(b.end_word, b.start_word)
        b.end_word = min(b.end_word, idx_max)
        if b.end_word < b.start_word:
            continue
        fixed.append(b)
        cursor = b.end_word + 1
        if cursor > idx_max:
            break
    if fixed and fixed[-1].end_word < idx_max:
        fixed[-1].end_word = idx_max
    return fixed


# ---------------------------------------------------------------- list explode
def _explode_lists(beats: list[RawBeat], words: list[W]) -> list[RawBeat]:
    """A list_item beat containing several comma-separated items becomes one
    beat per item — the LLM marks WHERE the list is; code guarantees the
    one-clip-per-item rhythm deterministically. Only list_item beats are
    touched; flow/escalation pass through unchanged."""
    wmap = {w.idx: w for w in words}
    out: list[RawBeat] = []
    for b in beats:
        if b.rhythm != "list_item" or b.end_word <= b.start_word:
            out.append(b)
            continue
        cut_after = [i for i in range(b.start_word, b.end_word)
                     if wmap[i].text.strip().rstrip('"').endswith((",", ";"))]
        if not cut_after:
            out.append(b)
            continue
        bounds = [b.start_word - 1] + cut_after + [b.end_word]
        for lo, hi in zip(bounds, bounds[1:]):
            if hi <= lo:
                continue
            out.append(RawBeat(lo + 1, hi, b.gist, b.tone, b.emphasis,
                               list(b.concrete_entities), b.visual_affinity,
                               "list_item"))
    return out


# ---------------------------------------------------------------- density fit
def _beat_seconds(b: RawBeat, wmap: dict[int, W]) -> float:
    return wmap[b.end_word].end_s - wmap[b.start_word].start_s


def _density_fit(beats: list[RawBeat], words: list[W], target_len: float) -> list[RawBeat]:
    wmap = {w.idx: w for w in words}

    def min_for(b: RawBeat) -> float:
        # Quick-cut beats (lists/escalations) may run short; only true slivers
        # get merged. Everything else holds >= MIN_BEAT_S on screen.
        return PROTECTED_MIN_S if b.rhythm in PROTECTED_RHYTHMS else MIN_BEAT_S

    # Merge too-short beats into the lower-emphasis neighbor.
    changed = True
    while changed and len(beats) > 1:
        changed = False
        for i, b in enumerate(beats):
            if _beat_seconds(b, wmap) < min_for(b):
                j = i - 1 if i > 0 and (i == len(beats) - 1 or
                                        beats[i - 1].emphasis <= beats[i + 1].emphasis) else i + 1
                lo, hi = sorted((i, j))
                merged = RawBeat(
                    start_word=beats[lo].start_word, end_word=beats[hi].end_word,
                    gist=beats[lo].gist or beats[hi].gist,
                    tone=beats[lo].tone,
                    emphasis=max(beats[lo].emphasis, beats[hi].emphasis),
                    concrete_entities=list({*beats[lo].concrete_entities,
                                            *beats[hi].concrete_entities}),
                    visual_affinity=beats[lo].visual_affinity,
                    rhythm=beats[lo].rhythm if beats[lo].rhythm in PROTECTED_RHYTHMS
                    else beats[hi].rhythm)
                beats = beats[:lo] + [merged] + beats[hi + 1:]
                changed = True
                break

    # Split beats longer than MAX_BEAT_S at the midpoint word (mechanical).
    out: list[RawBeat] = []
    for b in beats:
        if _beat_seconds(b, wmap) > MAX_BEAT_S and b.end_word > b.start_word:
            mid = (b.start_word + b.end_word) // 2
            out.append(RawBeat(b.start_word, mid, b.gist, b.tone, b.emphasis,
                               list(b.concrete_entities), b.visual_affinity,
                               b.rhythm))
            out.append(RawBeat(mid + 1, b.end_word, b.gist, b.tone, b.emphasis,
                               list(b.concrete_entities), b.visual_affinity,
                               b.rhythm))
        else:
            out.append(b)
    return out


# ---------------------------------------------------------------- finalize (timing)
def _snap(prev_w: W | None, next_w: W | None,
          silences: list[tuple[float, float]], duration: float) -> float:
    if prev_w is None:
        return 0.0
    if next_w is None:
        return round(min(duration, prev_w.end_s + 0.05), 3)
    lo, hi = prev_w.end_s, next_w.start_s
    for (s, e) in silences:
        if e > lo and s < hi and (min(e, hi) - max(s, lo)) >= SILENCE_CUT:
            return round((max(s, lo) + min(e, hi)) / 2, 3)
    if hi - lo >= GAP_CUT:
        return round((lo + hi) / 2, 3)
    return round(prev_w.end_s + 0.02, 3)


def _finalize(beats: list[RawBeat], words: list[W],
              silences: list[tuple[float, float]], duration: float) -> list[dict]:
    wmap = {w.idx: w for w in words}
    out: list[dict] = []
    for i, b in enumerate(beats):
        prev_before = wmap.get(b.start_word - 1)
        first = wmap[b.start_word]
        last = wmap[b.end_word]
        next_after = wmap.get(b.end_word + 1)
        start_s = 0.0 if i == 0 else _snap(prev_before, first, silences, duration)
        end_s = (round(min(duration, last.end_s + 0.05), 3) if i == len(beats) - 1
                 else _snap(last, next_after, silences, duration))
        text = " ".join(wmap[k].text.strip() for k in range(b.start_word, b.end_word + 1)
                        if k in wmap)
        out.append({
            "id": f"bt_{ULID()}",
            "start_s": round(start_s, 3),
            "end_s": round(end_s, 3),
            "word_start_idx": b.start_word,
            "word_end_idx": b.end_word,
            "text": text,
            "gist": b.gist or text[:120],
            "tone": b.tone,
            "concrete_entities": b.concrete_entities,
            "visual_affinity": b.visual_affinity,
            "rhythm": b.rhythm,
            "emphasis": round(float(b.emphasis), 3),
            "locked": False,
        })
    return out
