"""PixInsight implementation of ProcessingBackend.

Thin wrapper around the JSON-handoff + JS-script automation in
astro_utils/pixinsight_cli.py. Owns the PixInsight instance lifecycle
(start/stop) so pipeline code no longer manages a raw subprocess handle or
duplicates the "should I launch a new instance or reuse one that's already
running" logic that used to be copy-pasted between
stack_calibration_frames.stack() and calibrate_images.calibrate_lights().
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from astro_utils.pixinsight_cli import (
    calibrate_with_pixinsight,
    close_pixinsight,
    decide_gpu,
    integrate_with_pixinsight,
    launch_pixinsight,
    measure_with_pixinsight,
    postprocess_with_pixinsight,
    register_with_pixinsight,
    stack_with_pixinsight,
)

logger = logging.getLogger(__name__)


class PixInsightBackend:
    """ProcessingBackend backed by a PixInsight Core automation instance."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        config = config or {}
        configured_instance = config.get("pixinsight_instance")

        # A negative instance number means "reuse whatever instance is
        # already running" -- don't launch a new one, and pass instance=None
        # through to every stack()/calibrate() call so PixInsight targets its
        # currently active instance. Mirrors the force_new_inst logic that
        # used to be duplicated between stack() and calibrate_lights().
        self._launch_new_instance = not (
            configured_instance is not None and configured_instance < 0
        )
        self._instance = configured_instance if self._launch_new_instance else None
        self._exit_instance = bool(config.get("exit_instance", True))
        # How long to wait for PixInsight to signal completion before giving
        # up. A real stack of many large frames can legitimately take a
        # while, so this defaults generously; callers doing something small
        # and time-bounded (e.g. tests) can override per-call via **params.
        self._timeout_s = config.get("pixinsight_timeout_s", 1800)
        # auto | on | off -- see astro_utils.pixinsight_cli.decide_gpu
        self._gpu_mode = config.get("pixinsight_gpu", "auto")
        self._gpu_min_free_mb = int(config.get("pixinsight_gpu_min_free_mb", 2048))
        self._process = None

    def start(self) -> None:
        if not self._launch_new_instance or self._process is not None:
            return
        use_gpu, why = decide_gpu(self._gpu_mode, self._gpu_min_free_mb)
        (logger.info if use_gpu else logger.warning)(
            "PixInsight GPU acceleration %s: %s", "on" if use_gpu else "off", why)
        self._process = launch_pixinsight(instance=self._instance, use_gpu=use_gpu)

    def stop(self) -> None:
        if self._process is None:
            return
        if self._exit_instance:
            close_pixinsight(self._process)
        self._process = None

    def stack(self, files: List[Path], output_path: Path, **params) -> Path:
        output_path = Path(output_path)
        stack_with_pixinsight(
            files,
            output_path,
            instance=self._instance,
            timeout_s=params.get("timeout_s", self._timeout_s),
        )
        if not output_path.exists():
            raise RuntimeError(f"PixInsight stacking did not produce {output_path}")
        return output_path

    def calibrate(
        self,
        lights: List[Path],
        flat: Optional[Path],
        dark: Optional[Path],
        output_dir: Path,
        *,
        date_obs,
        **params,
    ) -> List[Path]:
        output_dir = Path(output_dir)
        calibrate_with_pixinsight(
            lights,
            flat,
            dark,
            output_dir,
            date_obs,
            instance=self._instance,
            timeout_s=params.get("timeout_s", self._timeout_s),
        )

        date_suffix = date_obs.strftime("%d%m%Y")
        outputs = []
        for light in lights:
            stem = Path(light).stem
            for ext in (".fits", ".xisf"):
                candidate = output_dir / f"{stem}__{date_suffix}_c{ext}"
                if candidate.exists():
                    outputs.append(candidate)
                    break
        return outputs

    def register(
        self,
        lights: List[Path],
        reference: Path,
        output_dir: Path,
        *,
        postfix: str = "_r",
        **params,
    ) -> Dict[Path, Path]:
        output_dir = Path(output_dir)
        register_with_pixinsight(
            lights,
            reference,
            output_dir,
            postfix=postfix,
            distortion_correction=params.get("distortion_correction", True),
            instance=self._instance,
            timeout_s=params.get("timeout_s", max(self._timeout_s, 7200)),
        )
        outputs = {}
        for light in lights:
            candidate = output_dir / f"{Path(light).stem}{postfix}.xisf"
            if candidate.exists():
                outputs[Path(light)] = candidate
        return outputs

    def integrate(
        self,
        groups: List[Dict[str, Any]],
        work_dir: Path,
        **params,
    ) -> List[Dict[str, Any]]:
        return integrate_with_pixinsight(
            groups,
            ln_dir=Path(work_dir),
            ln_scale=params.get("ln_scale", 1024),
            instance=self._instance,
            timeout_s=params.get("timeout_s", max(self._timeout_s, 4 * 3600)),
        )

    def measure(self, frames: List[Path], **params) -> Dict[Path, Dict[str, Any]]:
        return measure_with_pixinsight(
            frames,
            subframe_scale=params.get("subframe_scale", 1.0),
            camera_gain=params.get("camera_gain", 1.0),
            instance=self._instance,
            timeout_s=params.get("timeout_s", max(self._timeout_s, 3600)),
        )

    def postprocess(
        self,
        items: List[Dict[str, Any]],
        steps: List[Dict[str, Any]],
        **params,
    ) -> List[Dict[str, Any]]:
        return postprocess_with_pixinsight(
            items,
            steps,
            instance=self._instance,
            timeout_s=params.get("timeout_s", max(self._timeout_s, 2 * 3600)),
        )
