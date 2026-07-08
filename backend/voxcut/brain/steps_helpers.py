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
