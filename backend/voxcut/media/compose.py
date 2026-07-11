"""Render constants shared by proxy/export (spec §10).

Caption/ASS machinery removed: VOXCUT burns no text into video, ever.
Gap segments render as a plain CARD_BG background.
"""
from __future__ import annotations

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
