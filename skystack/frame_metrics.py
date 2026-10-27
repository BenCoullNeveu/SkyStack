"""Per-frame quality metrics, measured by the backend's SubframeSelector-style
`measure` and shown in the triage page's subframe selector, where cuts are
set per filter/exposure group (metrics only compare within one).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping


@dataclass(frozen=True)
class Metric:
    label: str
    better: str        # "low" or "high": which way is a better frame
    selectable: bool = True


METRICS: Dict[str, Metric] = {
    "fwhm": Metric("FWHM (px)", "low"),
    "eccentricity": Metric("Eccentricity", "low"),
    "stars": Metric("Stars", "high"),
    "psf_signal_weight": Metric("PSF signal weight", "high"),
    "psf_snr": Metric("PSF SNR", "high"),
    "snr": Metric("SNR estimate", "high"),
    "median": Metric("Median (background)", "low"),
    "noise": Metric("Noise", "low"),
    "mad": Metric("MAD", "low", selectable=False),
    "mstar": Metric("M*", "low", selectable=False),
    "psf_power": Metric("PSF flux power", "high", selectable=False),
    "weight": Metric("Weight", "high", selectable=False),
}


def frame_records(measurements: Mapping[Any, Mapping[str, Any]]) -> Iterable[Dict[str, Any]]:
    """Keep only known metric keys, coerced to float (NaN/None -> None)."""
    for frame, m in measurements.items():
        rec = {}
        for k in METRICS:
            try:
                v = float(m.get(k))
                rec[k] = v if v == v else None
            except (TypeError, ValueError):
                rec[k] = None
        yield frame, rec
