"""Backend-agnostic, format-agnostic frame metadata.

This centralizes the FITS/XISF header keyword mapping that today is
duplicated as module-level constants across calibrate_images.py,
stack_darks.py, stack_flats.py, stack_calibration_frames.py, and
astro_utils/fits_helpers.py, into one object built once from config.

Built on top of astro_utils.fits_helpers' format I/O (read_header_info,
parse_date_from_header) rather than its config-coupled
read_image_header_info, so this module has no import-time dependency on
config.yaml and can be exercised with an arbitrary keyword map in tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from astro_utils.fits_helpers import parse_date_from_header, read_header_info


@dataclass(frozen=True)
class FrameKeywordMap:
    """Maps logical frame attributes to FITS/XISF header keyword names."""

    filter: str = "FILTER"
    rotation: str = "ROTATANG"
    gain: str = "GAIN"
    offset: str = "OFFSET"
    exptime: str = "EXPTIME"
    temperature: str = "SET-TEMP"
    img_type: str = "IMAGETYP"

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "FrameKeywordMap":
        return cls(
            filter=config.get("header_filter_key", cls.filter),
            rotation=config.get("header_rotation_key", cls.rotation),
            gain=config.get("header_gain_key", cls.gain),
            offset=config.get("header_offset_key", cls.offset),
            exptime=config.get("header_exptime_key", cls.exptime),
            temperature=config.get("header_temperature_key", cls.temperature),
            img_type=config.get("header_img_type_key", cls.img_type),
        )


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class FrameMeta:
    """Typed, format-agnostic view of a single frame's header."""

    keymap: FrameKeywordMap
    filter: Optional[str] = None
    rotation: Optional[float] = None
    gain: Optional[int] = None
    offset: Optional[int] = None
    exptime: Optional[float] = None
    temperature: Optional[float] = None
    date_obs: Optional[date] = None
    path: Optional[Path] = None

    @classmethod
    def from_header_dict(
        cls,
        header: Mapping[str, Any],
        keymap: FrameKeywordMap,
        path: Optional[Path] = None,
    ) -> "FrameMeta":
        return cls(
            keymap=keymap,
            filter=header.get(keymap.filter),
            rotation=_safe_float(header.get(keymap.rotation)),
            gain=_safe_int(header.get(keymap.gain)),
            offset=_safe_int(header.get(keymap.offset)),
            exptime=_safe_float(header.get(keymap.exptime)),
            temperature=_safe_float(header.get(keymap.temperature)),
            date_obs=parse_date_from_header(hdr=dict(header)),
            path=path,
        )

    @classmethod
    def from_file(
        cls, path: Path, keymap: FrameKeywordMap, fmt: str = "auto"
    ) -> "FrameMeta":
        header = read_header_info(Path(path), fmt=fmt)
        return cls.from_header_dict(header, keymap, path=Path(path))
