"""Importing this package registers all job handlers into STEP_REGISTRY."""
from . import demo, transcribe  # noqa: F401

__all__ = ["demo", "transcribe"]
