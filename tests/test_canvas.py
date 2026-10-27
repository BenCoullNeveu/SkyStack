"""Tests for skystack/canvas.py -- bounding-box canvas + reference rendering.

Frames are synthetic TAN WCSs (no real plate solves needed): a 1"/px
scale, frames offset or rotated relative to each other by known amounts,
so the expected canvas size can be worked out by hand.
"""
from __future__ import annotations

import numpy as np
import pytest
from astropy.wcs import WCS

from skystack.canvas import (
    build_canvas,
    choose_orientation_frame,
    largest_rectangle,
    resample_to_canvas,
    write_reference,
)
from skystack.platesolve import SolvedFrame

SCALE = 1.0 / 3600.0  # 1 arcsec/px


def make_frame(name, width=400, height=300, dx=0.0, dy=0.0, rot_deg=0.0, ra=10.0, dec=0.0):
    """A frame whose centre is offset (dx, dy) *pixels* from (ra, dec)."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    t = np.radians(rot_deg)
    rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    w.wcs.cd = (np.diag([-SCALE, SCALE]) @ rot)
    # put crval at pixel centre minus the offset -> frame centre shifts by +offset on the sky
    w.wcs.crpix = [(width + 1) / 2.0 - dx, (height + 1) / 2.0 - dy]
    return SolvedFrame(path=__import__("pathlib").Path(name), wcs=w, width=width, height=height)


def test_largest_rectangle_finds_the_biggest_block():
    mask = np.zeros((6, 8), dtype=bool)
    mask[1:5, 2:7] = True   # 4x5 block...
    mask[0, 0:8] = True     # ...plus a full top row, which extends it to 5x5
    mask[5, 0] = True       # an isolated cell that must not matter
    assert largest_rectangle(mask) == (0, 2, 5, 7)


def test_largest_rectangle_empty_mask_returns_none():
    assert largest_rectangle(np.zeros((3, 3), dtype=bool)) is None


def test_single_frame_canvas_matches_frame_size():
    f = make_frame("a")
    canvas = build_canvas([f], f, mode="union")
    assert abs(canvas.width - f.width) <= 1
    assert abs(canvas.height - f.height) <= 1


def test_union_spans_both_offset_frames():
    a, b = make_frame("a"), make_frame("b", dx=100)
    canvas = build_canvas([a, b], a, mode="union")
    assert abs(canvas.width - 500) <= 2
    assert abs(canvas.height - 300) <= 2


def test_intersection_is_only_the_common_area():
    a, b = make_frame("a"), make_frame("b", dx=100)
    canvas = build_canvas([a, b], a, mode="intersection", grid_step=4)
    assert 300 - 8 <= canvas.width <= 300
    assert 300 - 8 <= canvas.height <= 300


def test_union_covers_a_frame_rotated_90_degrees():
    a, b = make_frame("a"), make_frame("b", rot_deg=90)
    canvas = build_canvas([a, b], a, mode="union")
    # 400x300 and 300x400 centred together -> 400x400
    assert abs(canvas.width - 400) <= 2
    assert abs(canvas.height - 400) <= 2


def test_disjoint_frames_have_no_intersection():
    a, b = make_frame("a"), make_frame("b", dx=1000)
    with pytest.raises(ValueError, match="no area in common"):
        build_canvas([a, b], a, mode="intersection")


def test_unknown_mode_rejected():
    a = make_frame("a")
    with pytest.raises(ValueError):
        build_canvas([a], a, mode="biggest")


def test_orientation_frame_is_the_most_central_one():
    left, mid, right = make_frame("l", dx=-200), make_frame("m"), make_frame("r", dx=200)
    assert choose_orientation_frame([left, mid, right]).path.name == "m"


def test_orientation_frame_prefers_luminance_when_present():
    mid, off = make_frame("m"), make_frame("off", dx=50)
    filters = {mid.path: "Ha", off.path: "L"}
    assert choose_orientation_frame([mid, off], filters).path.name == "off"


def test_resample_onto_own_canvas_is_identity_in_the_interior():
    f = make_frame("a", width=120, height=90)
    rng = np.random.default_rng(1)
    data = rng.random((90, 120)).astype(np.float32)
    canvas = build_canvas([f], f, mode="union")
    out = resample_to_canvas(data, f.wcs, canvas, sample_step=16)
    h, w = min(out.shape[0], 90), min(out.shape[1], 120)
    assert np.allclose(out[2:h - 2, 2:w - 2], data[2:h - 2, 2:w - 2], atol=1e-3)


def test_resample_places_offset_frame_on_the_right_side_and_zero_elsewhere():
    a, b = make_frame("a", 200, 100), make_frame("b", 200, 100, dx=100)
    canvas = build_canvas([a, b], a, mode="union")
    out = resample_to_canvas(np.ones((100, 200), np.float32), b.wcs, canvas)
    assert out[:, :90].max() == 0.0          # left 100 px: only frame a covers it
    assert out[10:90, 120:280].min() > 0.99  # frame b's area is filled


def test_write_reference_round_trips_through_xisf(tmp_path):
    from xisf import XISF

    f = make_frame("a", 40, 30)
    canvas = build_canvas([f], f, mode="union")
    data = np.arange(canvas.width * canvas.height, dtype=np.float32).reshape(canvas.height, canvas.width)
    out = write_reference(data, canvas, tmp_path / "ref.xisf", {"FILTER": "L"})

    meta = {}
    back = XISF.read(str(out), n=0, image_metadata=meta)
    assert np.allclose(np.squeeze(back), data)
    assert meta["FITSKeywords"]["FILTER"][0]["value"] == "L"
    assert meta["FITSKeywords"]["SSCANVAS"][0]["value"] == "union"
