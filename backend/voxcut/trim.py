"""Trim step: pure logic for cutting the raw recording before generate.

The operator reviews the raw upload (audio, or a facecam video) and removes
pauses, flubbed lines and retakes. Everything downstream — the VO master, the
facecam video, word times, silences — is then rebuilt from the RAW files with
the same list of cuts, so the trim can be redone until an edit exists.

Cuts are (start_s, end_s) in RAW time. They are snapped to the facecam frame
grid before use: the video loses whole frames and the audio loses exactly the
same duration, so lip sync cannot drift however many cuts there are.

Pure functions — no I/O — so the rules are unit-testable.
"""
from __future__ import annotations

import re

FPS = 30                 # the facecam is normalized to this at ingest
MIN_CUT_S = 2 / FPS      # anything shorter is not worth a splice
DEFAULT_MIN_PAUSE_S = 0.6
DEFAULT_KEEP_PAUSE_S = 0.25   # breathing room left where a pause was cut

_NORM = re.compile(r"[^a-z0-9']+")


def _norm(word: str) -> str:
    return _NORM.sub("", (word or "").lower()).strip("'")


# ------------------------------------------------------------------- cuts

def normalize_cuts(cuts: list, duration: float, fps: int = FPS) -> list[tuple[float, float]]:
    """Clamp to the recording, snap to the frame grid, sort, merge overlaps
    and drop slivers. The result is what every consumer applies."""
    snapped = []
    for c in cuts or []:
        try:
            a, b = float(c[0]), float(c[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(0.0, min(a, b)), min(duration, max(a, b))
        fa, fb = round(a * fps), round(b * fps)
        if fb - fa >= round(MIN_CUT_S * fps):
            snapped.append((fa, fb))
    snapped.sort()
    merged: list[list[int]] = []
    for fa, fb in snapped:
        if merged and fa <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], fb)
        else:
            merged.append([fa, fb])
    return [(round(fa / fps, 4), round(fb / fps, 4)) for fa, fb in merged]


def keep_ranges(cuts: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """The complement of the cuts: what survives, in raw time."""
    out, t = [], 0.0
    for a, b in cuts:
        if a > t:
            out.append((t, a))
        t = b
    if duration > t:
        out.append((t, duration))
    return out


def cut_total(cuts: list[tuple[float, float]]) -> float:
    return round(sum(b - a for a, b in cuts), 4)


def remap(t: float, cuts: list[tuple[float, float]]) -> float:
    """Raw time → trimmed time. A time inside a cut lands on the splice."""
    removed = 0.0
    for a, b in cuts:
        if t <= a:
            break
        removed += min(t, b) - a
    return round(t - removed, 3)


def remap_words(words: list[dict], cuts: list[tuple[float, float]]) -> list[dict]:
    """Drop words spoken inside a cut, shift the rest, renumber from 0."""
    out = []
    for w in words:
        mid = (w["start_s"] + w["end_s"]) / 2
        if any(a <= mid < b for a, b in cuts):
            continue
        s, e = remap(w["start_s"], cuts), remap(w["end_s"], cuts)
        out.append({**w, "idx": len(out), "start_s": s, "end_s": max(s, e)})
    return out


def remap_silences(silences: list, cuts: list[tuple[float, float]],
                   min_len: float = 0.1) -> list[list[float]]:
    out = []
    for s, e in silences or []:
        ns, ne = remap(s, cuts), remap(e, cuts)
        if ne - ns >= min_len:
            out.append([ns, ne])
    return out


# ------------------------------------------------------------ suggestions

def pause_cuts(silences: list, min_len: float = DEFAULT_MIN_PAUSE_S,
               keep: float = DEFAULT_KEEP_PAUSE_S) -> list[list[float]]:
    """Cut every silence of at least `min_len`, leaving `keep` seconds of it
    (split evenly either side) so speech never butts straight into speech."""
    out = []
    for s, e in silences or []:
        if e - s < min_len:
            continue
        a, b = s + keep / 2, e - keep / 2
        if b - a >= MIN_CUT_S:
            out.append([round(a, 3), round(b, 3)])
    return out


def retake_cuts(words: list[dict], max_n: int = 8, min_n: int = 3,
                phrase_gap_s: float = 0.35) -> list[dict]:
    """Lines said twice in a row — a restart or a retake. Proposes cutting
    the EARLIER take. Two patterns:

    - an exact repeat: the same 3–8 words immediately said again;
    - a false start: a phrase (bounded by pauses/punctuation) whose first
      three words also open the very next phrase.

    Suggestions only. A deliberate repeat ("no we, brother. No we, brother.")
    looks identical, which is why the operator confirms each one."""
    toks = [_norm(w.get("text", "")) for w in words]
    out: list[dict] = []
    covered_until = -1

    def add(i: int, j: int, reason: str) -> None:
        nonlocal covered_until
        start, end = words[i]["start_s"], words[j]["start_s"]
        if end - start < MIN_CUT_S:
            return
        text = " ".join((w.get("text") or "").strip() for w in words[i:j])
        out.append({"start_s": round(start, 3), "end_s": round(end, 3),
                    "text": text, "reason": reason})
        covered_until = j

    i = 0
    while i < len(words):
        hit = False
        for n in range(min(max_n, (len(words) - i) // 2), min_n - 1, -1):
            a, b = toks[i:i + n], toks[i + n:i + 2 * n]
            if a == b and all(a):
                add(i, i + n, "repeat")
                i += n
                hit = True
                break
        if not hit:
            i += 1

    # False starts: phrase boundaries at pauses or sentence punctuation.
    bounds = [0]
    for k in range(1, len(words)):
        gap = words[k]["start_s"] - words[k - 1]["end_s"]
        prev = (words[k - 1].get("text") or "").strip()
        if gap >= phrase_gap_s or prev.endswith((".", "?", "!", "—", "-")):
            bounds.append(k)
    bounds.append(len(words))
    phrases = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    taken = [(o["start_s"], o["end_s"]) for o in out]
    for (a0, a1), (b0, b1) in zip(phrases, phrases[1:]):
        if a1 - a0 < min_n or b1 - b0 < min_n or a1 - a0 > b1 - b0 + 2:
            continue
        if toks[a0:a0 + min_n] != toks[b0:b0 + min_n] or not all(toks[a0:a0 + min_n]):
            continue
        s, e = words[a0]["start_s"], words[b0]["start_s"]
        if any(s < te and ts < e for ts, te in taken):
            continue
        add(a0, b0, "false start")
    out.sort(key=lambda o: o["start_s"])
    return out
