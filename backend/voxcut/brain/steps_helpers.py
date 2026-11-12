"""Small shared helpers for brain steps."""
from __future__ import annotations


def brief_summary(brief: dict) -> str:
    parts = []
    if brief.get("title"):
        parts.append(f"Title: {brief['title']}")
    if brief.get("subject"):
        parts.append(f"Subject: {brief['subject']}")
    if brief.get("tone") and brief["tone"] != "infer":
        parts.append(f"Tone: {brief['tone']}")
    refs = brief.get("named_references") or []
    if refs:
        parts.append("References: " + ", ".join(
            f"{r.get('name')} ({r.get('hint')})" if r.get("hint") else r.get("name", "")
            for r in refs))
    if brief.get("notes"):
        parts.append(f"Notes: {brief['notes']}")
    return " | ".join(parts) or "(none provided)"


def beat_neighbors(beats: list[dict]) -> dict[str, tuple[str, str]]:
    """beat_id -> (previous line, next line) in timeline order. Judges and
    the single-beat planner get these so a fragment like "and then it just
    left" is read as what it MEANS in the script, not as bare keywords."""
    ordered = sorted(beats, key=lambda b: b.get("start_s", 0.0))
    texts = [(b.get("text") or b.get("gist", "")).strip() for b in ordered]
    out: dict[str, tuple[str, str]] = {}
    for i, b in enumerate(ordered):
        out[b["id"]] = (texts[i - 1] if i > 0 else "",
                        texts[i + 1] if i + 1 < len(ordered) else "")
    return out
