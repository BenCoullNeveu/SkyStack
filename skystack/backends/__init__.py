"""Backend selection.

Mirrors astro_triage/backend.py's select_backend() pattern (used there for
the CPU/MP/GPU compute backend), applied to the stacking/calibration engine.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import ProcessingBackend
from .pixinsight import PixInsightBackend

_BACKENDS = {
    "pixinsight": PixInsightBackend,
}


def select_backend(config: Mapping[str, Any]) -> ProcessingBackend:
    name = str(config.get("processing_backend", "pixinsight")).lower()
    try:
        backend_cls = _BACKENDS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown processing_backend {name!r}. Available: {sorted(_BACKENDS)}"
        ) from exc
    return backend_cls(config)
