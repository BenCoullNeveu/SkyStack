"""ProcessingBackend interface.

Any stacking/calibration engine (PixInsight, PySiril, native Python) must
implement this protocol. Pipeline code (stack_calibration_frames.py,
calibrate_images.py, etc.) should eventually depend only on this interface,
never on a specific backend's API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ProcessingBackend(Protocol):
    def start(self) -> None:
        """Prepare the backend for use (e.g. launch an application instance).

        Must be safe to call more than once (idempotent).
        """
        ...

    def stop(self) -> None:
        """Release any resources acquired by start().

        Must be safe to call even if start() was never called.
        """
        ...

    def stack(self, files: List[Path], output_path: Path, **params) -> Path:
        """Integrate `files` into a single master frame at `output_path`.

        Returns the output path. Must raise on failure rather than
        returning a path that does not exist.
        """
        ...

    def calibrate(
        self,
        lights: List[Path],
        flat: Optional[Path],
        dark: Optional[Path],
        output_dir: Path,
        **params,
    ) -> List[Path]:
        """Calibrate `lights` against the given master flat/dark.

        Returns the list of successfully calibrated output paths, which may
        be shorter than `lights` if some frames failed.
        """
        ...

    def register(
        self,
        lights: List[Path],
        reference: Path,
        output_dir: Path,
        **params,
    ) -> Dict[Path, Path]:
        """Register `lights` onto `reference`'s geometry.

        Returns {input light: registered output} for every frame that
        registered successfully; frames that failed are left out.
        """
        ...

    def integrate(
        self,
        groups: List[Dict[str, Any]],
        work_dir: Path,
        **params,
    ) -> List[Dict[str, Any]]:
        """Normalize and integrate already-registered lights, one master per group.

        Each group is {"name", "files", "output_path", "rejection"}. Returns
        one {"name", "status": "ok"|"error", "output_path"|"error"} dict per
        group; one group failing must not stop the others.
        """
        ...

    def postprocess(
        self,
        items: List[Dict[str, Any]],
        steps: List[Dict[str, Any]],
        **params,
    ) -> List[Dict[str, Any]]:
        """Apply `steps` ({"name", "process", "params"}) in order to each item
        ({"input", "output"}), writing the result to "output". Returns one
        {"input", "status": "ok"|"error", "output"|"error"} per item; one image
        failing must not stop the others.
        """
        ...

    def measure(self, frames: List[Path], **params) -> Dict[Path, Dict[str, Any]]:
        """Measure per-frame quality metrics (see skystack.frame_metrics.METRICS).

        Returns {frame: {metric name: value}} for every frame that could be
        measured; frames that failed are left out.
        """
        ...
