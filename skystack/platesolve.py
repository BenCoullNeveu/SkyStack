"""ASTAP plate solving.

Solves calibrated frames with ASTAP's command-line solver and returns an
astropy WCS for each. Used to work out every frame's sky footprint so the
registration canvas (see skystack/canvas.py) can cover the whole session.

ASTAP always writes a `<image>.ini` sidecar next to the image it solved
(PLTSOLVD=T/F plus the CRPIX/CRVAL/CD solution). That sidecar doubles as
the cache: a frame whose .ini already says PLTSOLVD=T is not re-solved.
The image file itself is never modified (no `-update`).
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import numpy as np
from astropy.wcs import WCS

from astro_utils.fits_helpers import read_header_info

logger = logging.getLogger(__name__)

DEFAULT_ASTAP_PATH = r"C:\Program Files\astap\astap_cli.exe"


@dataclass
class SolvedFrame:
    path: Path
    wcs: WCS
    width: int
    height: int

    @property
    def pixel_scale_deg(self) -> float:
        """Mean pixel scale in degrees/pixel."""
        return float(np.sqrt(abs(np.linalg.det(self.wcs.pixel_scale_matrix))))


def ini_path_for(image: Path) -> Path:
    return Path(image).with_suffix(".ini")


def parse_astap_ini(text: str) -> Dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip().upper()] = value.strip()
    return out


def wcs_from_astap_ini(values: Mapping[str, str]) -> Optional[WCS]:
    """Build a TAN WCS from a parsed ASTAP .ini, or None if unsolved."""
    if values.get("PLTSOLVD", "F").upper() != "T":
        return None
    try:
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crpix = [float(values["CRPIX1"]), float(values["CRPIX2"])]
        w.wcs.crval = [float(values["CRVAL1"]), float(values["CRVAL2"])]
        w.wcs.cd = [
            [float(values["CD1_1"]), float(values["CD1_2"])],
            [float(values["CD2_1"]), float(values["CD2_2"])],
        ]
        return w
    except (KeyError, ValueError) as e:
        logger.warning("ASTAP .ini is marked solved but incomplete: %s", e)
        return None


def _dimensions(values: Mapping[str, str], image: Path):
    # Some ASTAP versions write e.g. "DIMENSIONS=6248 x 4176"
    dims = values.get("DIMENSIONS")
    if dims and "x" in dims:
        w, _, h = dims.partition("x")
        try:
            return int(w), int(h)
        except ValueError:
            pass
    if image.suffix.lower() == ".xisf":
        # PixInsight doesn't keep NAXIS1/2 among an XISF's FITS keywords;
        # the size lives in the image block's geometry (width:height:channels).
        from xisf import XISF
        try:
            geometry = XISF(str(image)).get_images_metadata()[0]["geometry"]
            return int(geometry[0]), int(geometry[1])
        except Exception as e:
            logger.warning("Could not read XISF geometry of %s: %s", image.name, e)
            return None
    header = read_header_info(image)
    w, h = header.get("NAXIS1"), header.get("NAXIS2")
    if w is not None and h is not None:
        return int(float(w)), int(float(h))
    return None


def _hint_args(header: Mapping[str, Any]) -> List[str]:
    """RA/Dec/FOV hints from the header so ASTAP searches a small area.

    ASTAP takes RA in hours and declination as south-pole distance
    (dec + 90). Any hint that can't be worked out is left for ASTAP to
    find itself.
    """
    args: List[str] = []

    def num(key):
        try:
            return float(header.get(key))
        except (TypeError, ValueError):
            return None

    ra, dec = num("RA"), num("DEC")
    if ra is not None and dec is not None:
        args += ["-ra", f"{ra / 15.0:.6f}", "-spd", f"{dec + 90.0:.6f}"]

    focal, pix, height = num("FOCALLEN"), num("YPIXSZ"), num("NAXIS2")
    binning = num("YBINNING") or 1.0
    if focal and pix and height:
        fov_deg = height * pix * binning / focal * 206.265 / 3600.0
        args += ["-fov", f"{fov_deg:.4f}"]
    else:
        args += ["-fov", "0"]
    return args


def load_solution(image: Path) -> Optional[SolvedFrame]:
    """Return the cached ASTAP solution for `image`, if there is one."""
    ini = ini_path_for(image)
    if not ini.exists():
        return None
    values = parse_astap_ini(ini.read_text(errors="replace"))
    wcs = wcs_from_astap_ini(values)
    if wcs is None:
        return None
    dims = _dimensions(values, Path(image))
    if dims is None:
        logger.warning("Solved %s but could not work out its dimensions", image)
        return None
    return SolvedFrame(Path(image), wcs, dims[0], dims[1])


# Header keywords carried into the temporary FITS handed to ASTAP
_HINT_KEYS = ("RA", "DEC", "OBJCTRA", "OBJCTDEC", "FOCALLEN", "XPIXSZ", "YPIXSZ",
              "XBINNING", "YBINNING", "DATE-OBS", "EXPTIME", "FILTER")


def _write_fits_for_astap(image: Path, header: Mapping[str, Any], dest: Path) -> Dict[str, Any]:
    """Write `image` as a plain float32 FITS for ASTAP to read.

    ASTAP cannot read the XISF files PixInsight's calibration writes, so
    XISF frames are solved from a temporary FITS copy. Row 0 of the array
    becomes FITS row 1, the same convention every other reader here uses,
    so the solution applies to the original file unchanged. Returns the
    header (with NAXIS1/2 filled in) for building ASTAP's hints.
    """
    from astropy.io import fits
    from QuickBlink import load_image

    data = load_image(image)
    if data.ndim == 3:
        data = data.mean(axis=2)
    hi = float(np.nanmax(data)) or 1.0
    if hi <= 1.0:  # normalized [0, 1] data -> 16-bit-like range ASTAP expects
        data = data * 65535.0
    hdu = fits.PrimaryHDU(np.ascontiguousarray(data, dtype=np.float32))
    for key in _HINT_KEYS:
        value = header.get(key)
        if value in (None, ""):
            continue
        try:
            value = float(value) if key not in ("OBJCTRA", "OBJCTDEC", "DATE-OBS", "FILTER") else str(value)
        except (TypeError, ValueError):
            value = str(value)
        hdu.header[key] = value
    hdu.writeto(dest, overwrite=True)
    return {**header, "NAXIS1": data.shape[1], "NAXIS2": data.shape[0]}


def solve_frame(
    image: Path,
    astap_path: str = DEFAULT_ASTAP_PATH,
    search_radius_deg: float = 10.0,
    timeout_s: float = 120,
    force: bool = False,
) -> Optional[SolvedFrame]:
    image = Path(image)
    if not force:
        cached = load_solution(image)
        if cached is not None:
            return cached

    header = read_header_info(image)
    with tempfile.TemporaryDirectory(prefix="skystack_astap_") as tmp:
        solve_path = image
        if image.suffix.lower() == ".xisf":
            solve_path = Path(tmp) / "frame.fits"
            header = _write_fits_for_astap(image, header, solve_path)

        cmd = [astap_path, "-f", str(solve_path), "-r", str(search_radius_deg), "-z", "0"]
        cmd += _hint_args(header)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            logger.warning("ASTAP timed out on %s", image.name)
            return None

        produced = ini_path_for(solve_path)
        if solve_path != image and produced.exists():
            ini_path_for(image).write_text(produced.read_text(errors="replace"))

    if result.returncode != 0:
        logger.warning("ASTAP could not solve %s (exit %s)", image.name, result.returncode)
        return None
    return load_solution(image)


def solve_frames(
    images: Iterable[Path],
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[Path, Optional[SolvedFrame]]:
    """Solve frames in parallel. Unsolved frames map to None."""
    cfg = dict((config or {}).get("platesolve", {}) or {})
    astap_path = cfg.get("astap_path", DEFAULT_ASTAP_PATH)
    radius = cfg.get("search_radius_deg", 10.0)
    workers = int(cfg.get("workers", 4))
    timeout_s = cfg.get("timeout_s", 120)

    images = [Path(p) for p in images]
    results: Dict[Path, Optional[SolvedFrame]] = {}

    def _solve(p):
        return p, solve_frame(p, astap_path, radius, timeout_s)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (p, solved) in enumerate(pool.map(_solve, images), start=1):
            results[p] = solved
            logger.info("[%d/%d] %s %s", i, len(images), "solved" if solved else "UNSOLVED", p.name)

    n_ok = sum(1 for v in results.values() if v is not None)
    logger.info("Plate solved %d / %d frames", n_ok, len(images))
    return results
