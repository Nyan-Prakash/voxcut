"""Local text embeddings for semantic matching (spec §9.2).

fastembed / bge-small-en-v1.5 (ONNX, CPU-fast). Falls back to a rapidfuzz
token-set ratio if fastembed or the model is unavailable, so the pipeline never
hard-depends on the model download.
"""
from __future__ import annotations

import numpy as np

_MODEL = None
_TRIED = False
_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def _model():
    global _MODEL, _TRIED
    if _MODEL is None and not _TRIED:
        _TRIED = True
        try:
            from fastembed import TextEmbedding
            _MODEL = TextEmbedding(model_name=_MODEL_NAME)
        except Exception:  # noqa: BLE001
            _MODEL = None
    return _MODEL


def available() -> bool:
    return _model() is not None


def embed(texts: list[str]) -> np.ndarray | None:
    """Return L2-normalized embeddings (n, d), or None if unavailable."""
    m = _model()
    if m is None or not texts:
        return None
    vecs = np.array(list(m.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def cosine_matrix(query_vec: np.ndarray, doc_vecs: np.ndarray) -> np.ndarray:
    """query_vec (d,) already normalized; doc_vecs (n,d) normalized → (n,)."""
    return doc_vecs @ query_vec


def fuzzy_sim(query: str, docs: list[str]) -> np.ndarray:
    """Fallback similarity 0..1 using token-set ratio."""
    from rapidfuzz import fuzz
    return np.array([fuzz.token_set_ratio(query.lower(), d.lower()) / 100.0
                     for d in docs], dtype=np.float32)
