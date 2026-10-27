"""Registration canvas: the bounding box of a session's sky coverage.

Given the plate solutions of every frame in a session/target, this builds
a single tangent-plane canvas that covers either:

- the UNION of all frame footprints (default -- keeps every pixel of data,
  edges are ragged where only some frames reach), or
- the INTERSECTION (the largest axis-aligned rectangle every frame covers).

It then renders a reference image on that canvas by resampling one real
frame onto it. PixInsight's StarAlignment registers every light to that
reference, so all registered lights share the canvas geometry. The
resampled frame only needs to supply stars for matching; the canvas size
and orientation are what matter.

Coverage is worked out on a coarse grid (one cell per `grid_step` canvas
pixels) rather than with exact polygon clipping. That is plenty accurate
for picking a crop and keeps the intersection case simple (largest
rectangle in a binary mask).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
from astropy.wcs import WCS
from matplotlib.path import Path as MplPath
from scipy.ndimage import map_coordinates

from skystack.platesolve import SolvedFrame

logger = logging.getLogger(__name__)

CANVAS_MODES = ("union", "intersection")


@dataclass
class Canvas:
    wcs: WCS
    width: int
    height: int
    mode: str
    n_frames: int

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "width": self.width,
            "height": self.height,
            "n_frames": self.n_frames,
            "crval": [float(v) for v in self.wcs.wcs.crval],
            "crpix": [float(v) for v in self.wcs.wcs.crpix],
            "cd": np.asarray(self.wcs.wcs.cd).tolist(),
        }


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def frame_outline_pixels(width: int, height: int, n_per_side: int = 8) -> np.ndarray:
    """Points around a frame's outer pixel edges, as (N, 2) x/y (0-based)."""
    x0, y0, x1, y1 = -0.5, -0.5, width - 0.5, height - 0.5
    t = np.linspace(0.0, 1.0, n_per_side, endpoint=False)
    bottom = np.column_stack([x0 + t * (x1 - x0), np.full_like(t, y0)])
    right = np.column_stack([np.full_like(t, x1), y0 + t * (y1 - y0)])
    top = np.column_stack([x1 - t * (x1 - x0), np.full_like(t, y1)])
    left = np.column_stack([np.full_like(t, x0), y1 - t * (y1 - y0)])
    return np.vstack([bottom, right, top, left])


def _unit_vector(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def frame_center_world(frame: SolvedFrame) -> Tuple[float, float]:
    ra, dec = frame.wcs.pixel_to_world_values((frame.width - 1) / 2.0, (frame.height - 1) / 2.0)
    return float(ra), float(dec)


def mean_center(frames: Sequence[SolvedFrame]) -> Tuple[float, float]:
    vec = sum(_unit_vector(*frame_center_world(f)) for f in frames)
    vec = vec / np.linalg.norm(vec)
    ra = math.degrees(math.atan2(vec[1], vec[0])) % 360.0
    dec = math.degrees(math.asin(vec[2]))
    return ra, dec


def largest_rectangle(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """Largest all-True axis-aligned rectangle in a 2D bool mask.

    Returns (row0, col0, row1, col1) with row1/col1 exclusive, or None if
    the mask has no True cells. Classic histogram-stack algorithm, O(rows*cols).
    """
    rows, cols = mask.shape
    heights = np.zeros(cols, dtype=int)
    best, best_area = None, 0
    for r in range(rows):
        heights = np.where(mask[r], heights + 1, 0)
        stack: List[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            while stack and heights[stack[-1]] >= h:
                top = stack.pop()
                height = heights[top]
                left = stack[-1] + 1 if stack else 0
                area = height * (c - left)
                if area > best_area:
                    best_area = area
                    best = (r - height + 1, left, r + 1, c)
            stack.append(c)
    return best


# ---------------------------------------------------------------------------
# Canvas construction
# ---------------------------------------------------------------------------

def choose_orientation_frame(
    frames: Sequence[SolvedFrame],
    filters: Optional[dict] = None,
    preferred_filters: Iterable[str] = ("L",),
) -> SolvedFrame:
    """Pick the frame whose centre is closest to the session's mean centre.

    Frames in a preferred filter (luminance by default -- most stars) are
    considered first when any exist. `filters` maps frame path -> filter.
    """
    if not frames:
        raise ValueError("No solved frames to choose from")
    preferred = {f.upper() for f in preferred_filters}
    pool = list(frames)
    if filters:
        in_pref = [f for f in frames if str(filters.get(f.path, "")).upper() in preferred]
        if in_pref:
            pool = in_pref

    center = _unit_vector(*mean_center(frames))
    return max(pool, key=lambda f: float(np.dot(_unit_vector(*frame_center_world(f)), center)))


def build_canvas(
    frames: Sequence[SolvedFrame],
    orientation: SolvedFrame,
    mode: str = "union",
    grid_step: int = 16,
) -> Canvas:
    """Build the canvas covering `frames`.

    The canvas uses `orientation`'s pixel scale and rotation (so a session
    shot at one rotation gets a canvas no bigger than it needs), centred on
    the mean centre of all frames.
    """
    if mode not in CANVAS_MODES:
        raise ValueError(f"Unknown canvas mode {mode!r}; use one of {CANVAS_MODES}")
    if not frames:
        raise ValueError("No solved frames to build a canvas from")

    base = WCS(naxis=2)
    base.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    base.wcs.crval = list(mean_center(frames))
    base.wcs.crpix = [1.0, 1.0]  # canvas pixel (0, 0) sits on crval until cropped
    base.wcs.cd = orientation.wcs.pixel_scale_matrix

    polygons = []
    for f in frames:
        outline = frame_outline_pixels(f.width, f.height)
        ra, dec = f.wcs.pixel_to_world_values(outline[:, 0], outline[:, 1])
        x, y = base.world_to_pixel_values(ra, dec)
        polygons.append(np.column_stack([x, y]))

    # Outer pixel edges, in base-canvas pixel coordinates
    allpts = np.vstack(polygons)
    xmin, ymin = allpts.min(axis=0)
    xmax, ymax = allpts.max(axis=0)

    if mode == "union":
        x0, y0, x1, y1 = xmin, ymin, xmax, ymax
    else:
        # Coverage count at the centre of each coarse cell
        gx = np.arange(xmin + grid_step / 2.0, xmax, grid_step)
        gy = np.arange(ymin + grid_step / 2.0, ymax, grid_step)
        GX, GY = np.meshgrid(gx, gy)
        pts = np.column_stack([GX.ravel(), GY.ravel()])
        coverage = np.zeros(pts.shape[0], dtype=int)
        for poly in polygons:
            coverage += MplPath(poly).contains_points(pts)
        rect = largest_rectangle(coverage.reshape(GX.shape) >= len(frames))
        if rect is None:
            raise ValueError(
                "The frames have no area in common; use canvas mode 'union' "
                "or pick a reference frame explicitly."
            )
        r0, c0, r1, c1 = rect
        x0, y0 = xmin + c0 * grid_step, ymin + r0 * grid_step
        x1, y1 = xmin + c1 * grid_step, ymin + r1 * grid_step

    # Canvas pixel 0 is centred half a pixel inside the outer edge x0.
    # Base pixel p (0-based) sits at 1-based crpix 1 + p, so the new crpix
    # (where crval lands) is 1 + (0 - (x0 + 0.5)).
    canvas_wcs = base.deepcopy()
    canvas_wcs.wcs.crpix = [0.5 - x0, 0.5 - y0]
    width, height = int(round(x1 - x0)), int(round(y1 - y0))

    ref_area = orientation.width * orientation.height
    logger.info(
        "Canvas (%s) is %dx%d px, %.2fx the area of one frame",
        mode, width, height, width * height / ref_area,
    )
    return Canvas(canvas_wcs, width, height, mode, len(frames))


# ---------------------------------------------------------------------------
# Reference image rendering
# ---------------------------------------------------------------------------

def resample_to_canvas(
    data: np.ndarray,
    source_wcs: WCS,
    canvas: Canvas,
    sample_step: int = 32,
    chunk_rows: int = 512,
) -> np.ndarray:
    """Resample a 2D frame onto the canvas (bilinear, zeros outside).

    The exact WCS mapping is only evaluated on a coarse grid (every
    `sample_step` pixels) and interpolated in between -- both projections
    are smooth, so this is effectively exact and orders of magnitude faster
    than transforming tens of millions of pixels through astropy.
    """
    H, W = canvas.height, canvas.width
    cy = np.arange(0, H + sample_step, sample_step, dtype=float)
    cx = np.arange(0, W + sample_step, sample_step, dtype=float)
    CX, CY = np.meshgrid(cx, cy)
    ra, dec = canvas.wcs.pixel_to_world_values(CX, CY)
    SX, SY = source_wcs.world_to_pixel_values(ra, dec)
    SX, SY = SX.astype(np.float32), SY.astype(np.float32)

    out = np.zeros((H, W), dtype=np.float32)
    cols = (np.arange(W, dtype=np.float32) / sample_step)
    for r0 in range(0, H, chunk_rows):
        r1 = min(H, r0 + chunk_rows)
        rows = (np.arange(r0, r1, dtype=np.float32) / sample_step)
        RR, CC = np.meshgrid(rows, cols, indexing="ij")
        sx = map_coordinates(SX, [RR, CC], order=1, mode="nearest")
        sy = map_coordinates(SY, [RR, CC], order=1, mode="nearest")
        out[r0:r1] = map_coordinates(data, [sy, sx], order=1, mode="constant", cval=0.0)
    return out


def write_reference(
    data: np.ndarray,
    canvas: Canvas,
    out_path: Path,
    extra_keywords: Optional[dict] = None,
) -> Path:
    """Write the reference as XISF so PixInsight reads it in the same row
    order as the calibrated XISF lights it will be matched against."""
    from xisf import XISF

    keywords = {}
    for card in canvas.wcs.to_header().cards:
        keywords[card.keyword] = [{"value": str(card.value), "comment": str(card.comment)}]
    keywords["NAXIS1"] = [{"value": str(canvas.width), "comment": ""}]
    keywords["NAXIS2"] = [{"value": str(canvas.height), "comment": ""}]
    for k, v in (extra_keywords or {}).items():
        if v is not None:
            keywords[k] = [{"value": str(v), "comment": ""}]
    keywords["SSCANVAS"] = [{"value": canvas.mode, "comment": "SkyStack registration canvas mode"}]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    XISF.write(
        str(out_path),
        np.atleast_3d(np.ascontiguousarray(data, dtype=np.float32)),
        creator_app="SkyStack",
        image_metadata={"FITSKeywords": keywords},
        xisf_metadata={},
        codec=None,
        shuffle=True,
    )
    return out_path


def render_reference(
    source: SolvedFrame,
    canvas: Canvas,
    out_path: Path,
    extra_keywords: Optional[dict] = None,
) -> Path:
    from QuickBlink import load_image

    data = load_image(source.path)
    if data.ndim == 3:
        data = data.mean(axis=2)
    resampled = resample_to_canvas(data, source.wcs, canvas)
    out = write_reference(resampled, canvas, out_path, extra_keywords)

    meta_path = Path(out_path).with_suffix(".json")
    meta_path.write_text(json.dumps({**canvas.to_dict(), "source_frame": str(source.path)}, indent=2))
    logger.info("Wrote registration reference %s", out)
    return out
