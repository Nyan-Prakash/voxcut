"""faster-whisper wrapper with word timestamps, VAD, and boundary refinement.

Spec §5.2–5.3: local ASR, word-level timestamps, Silero VAD (prevents silence
hallucination AND yields a silence map used by beat segmentation), and a cheap
DSP boundary-snap pass so cuts land in the breath before the word.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_MODEL_CACHE: dict[str, object] = {}

# Quality tier → model. CTranslate2 runs on CPU on macOS (no MPS), so we bias to
# smaller models unless the user picks "best". VOXCUT_ASR_MODEL overrides (tests).
TIER_MODELS = {
    "fast": "base",
    "balanced": "medium",
    "best": "large-v3",
}


@dataclass
class WordT:
    idx: int
    text: str
    start_s: float
    end_s: float
    confidence: float


@dataclass
class Transcript:
    words: list[WordT] = field(default_factory=list)
    silences: list[tuple[float, float]] = field(default_factory=list)
    language: str = "en"

    @property
    def text(self) -> str:
        return "".join(w.text for w in self.words).strip()


def _pick_model(tier: str) -> str:
    import os
    override = os.environ.get("VOXCUT_ASR_MODEL")
    if override:
        return override
    return TIER_MODELS.get(tier, "medium")


def _get_model(name: str):
    if name not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        _MODEL_CACHE[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL_CACHE[name]


def transcribe(asr_wav: Path, tier: str = "balanced",
               progress=None) -> Transcript:
    model = _get_model(_pick_model(tier))
    segments, info = model.transcribe(
        str(asr_wav),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 250},
        condition_on_previous_text=False,   # slangy VO → prevents repetition loops
        beam_size=5,
        language="en",
    )

    words: list[WordT] = []
    total = max(info.duration, 0.001)
    for seg in segments:
        for w in seg.words or []:
            words.append(WordT(
                idx=len(words),
                text=w.word,
                start_s=round(w.start, 3),
                end_s=round(w.end, 3),
                confidence=round(getattr(w, "probability", 1.0), 3),
            ))
        if progress:
            progress(min(0.99, seg.end / total))

    silences = _silence_map(words, info.duration)
    _snap_boundaries(words, asr_wav, silences)
    return Transcript(words=words, silences=silences, language=info.language or "en")


def _silence_map(words: list[WordT], duration: float,
                 min_gap: float = 0.12) -> list[tuple[float, float]]:
    """Gaps between consecutive words ≥ min_gap → silence regions (§5.2)."""
    sil: list[tuple[float, float]] = []
    prev_end = 0.0
    for w in words:
        if w.start_s - prev_end >= min_gap:
            sil.append((round(prev_end, 3), round(w.start_s, 3)))
        prev_end = max(prev_end, w.end_s)
    if duration - prev_end >= min_gap:
        sil.append((round(prev_end, 3), round(duration, 3)))
    return sil


def _snap_boundaries(words: list[WordT], asr_wav: Path,
                     silences: list[tuple[float, float]], win_s: float = 0.12) -> None:
    """Snap each word boundary to the nearest local RMS minimum within ±win_s (§5.3).

    Cheap DSP pass that fixes most 'cut lands mid-syllable' artifacts without a
    forced-aligner dependency.
    """
    if not words:
        return
    with wave.open(str(asr_wav), "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return

    hop = int(0.010 * sr)  # 10 ms
    win = int(win_s * sr)

    def nearest_min(t: float) -> float:
        center = int(t * sr)
        lo = max(0, center - win)
        hi = min(audio.size, center + win)
        if hi - lo < hop:
            return t
        # RMS over 10ms hops in the window; pick the quietest hop center.
        best_i, best_v = center, float("inf")
        for i in range(lo, hi, hop):
            seg = audio[i:i + hop]
            v = float(np.sqrt(np.mean(seg * seg))) if seg.size else 1.0
            if v < best_v:
                best_v, best_i = v, i + hop // 2
        return round(best_i / sr, 3)

    for w in words:
        w.start_s = nearest_min(w.start_s)
        w.end_s = max(w.start_s + 0.02, nearest_min(w.end_s))
