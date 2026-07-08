"""Filtergraph + ASS caption generation (spec §10).

Captions are rendered as ASS subtitles (styled, positioned) rather than fragile
drawtext chains. Caption cards are a solid background + a centered ASS line.
"""
from __future__ import annotations

from pathlib import Path

# aspect → (proxy_w, proxy_h, full_w, full_h)
RES = {
    "16:9": (640, 360, 1920, 1080),
    "9:16": (360, 640, 1080, 1920),
}
CARD_BG = "0x0d0f13"
FPS = 30


def dims(aspect: str, proxy: bool) -> tuple[int, int]:
    w9, h9, wf, hf = RES.get(aspect, RES["16:9"])
    return (w9, h9) if proxy else (wf, hf)


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\n", "\\N").strip())


def _ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _styles(h: int) -> str:
    """ASS style block scaled to the render height."""
    def fs(base: int) -> int:
        return max(10, round(base * h / 360))
    # Format: Name,Font,Size,Primary,Secondary,Outline,Back,Bold,Italic,Under,
    # Strike,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Align,ML,MR,MV,Enc
    common = "Arial"
    return "\n".join([
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding"),
        f"Style: meme_top,{common},{fs(46)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{fs(4)},1,8,40,40,40,1",
        f"Style: meme_bottom,{common},{fs(46)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{fs(4)},1,2,40,40,50,1",
        f"Style: subtitle,{common},{fs(30)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{fs(3)},1,2,60,60,45,1",
        f"Style: label,{common},{fs(24)},&H0000E0FF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{fs(2)},0,1,40,40,40,1",
        f"Style: card,{common},{fs(52)},&H00FFFFFF,&H000000FF,&H00202632,&H00000000,-1,0,0,0,100,100,0,0,1,{fs(3)},0,5,80,80,80,1",
    ])


def ass_header(w: int, h: int) -> str:
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        _styles(h),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])


def card_ass(text: str, dur: float, w: int, h: int, style: str = "card") -> str:
    line = (f"Dialogue: 0,{_ts(0)},{_ts(dur)},{style},,0,0,0,,"
            f"{{\\fad(120,120)}}{_esc(text)}")
    return ass_header(w, h) + "\n" + line + "\n"


def write_card_ass(text: str, dur: float, w: int, h: int, style: str,
                   out: Path) -> Path:
    out.write_text(card_ass(text, dur, w, h, style))
    return out


def timeline_ass(events: list[dict], w: int, h: int) -> str:
    """Whole-timeline caption overlay (used when captions burn over clips)."""
    lines = []
    for ev in events:
        cap = ev.get("caption") or {}
        if not cap.get("enabled") or not cap.get("text"):
            continue
        style = cap.get("style", "subtitle")
        lines.append(f"Dialogue: 0,{_ts(ev['start_s'])},{_ts(ev['end_s'])},"
                     f"{style},,0,0,0,,{{\\fad(80,80)}}{_esc(cap['text'])}")
    return ass_header(w, h) + "\n" + "\n".join(lines) + "\n"
