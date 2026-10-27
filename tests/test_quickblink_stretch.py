"""Tests for QuickBlink.py's auto_stretch/mtf -- the PixInsight AutoSTF-style
preview stretch. Verifies the math against the algorithm's known properties
rather than pixel-exact output (there's no independent reference to compare
against here, just the algorithm's own invariants).
"""
from __future__ import annotations

import numpy as np
import pytest

from QuickBlink import _stretch_params, auto_stretch, downsample_linear, mtf


def test_mtf_self_inverse_property():
    """mtf(a, b) == c implies mtf(c, b) == a -- auto_stretch's midtones
    balance calculation (_stretch_params) relies on this holding exactly."""
    a, b = 0.5625, 0.3
    c = mtf(a, np.array([b]))[0]
    back = mtf(c, np.array([b]))[0]
    assert back == pytest.approx(a, abs=1e-9)


def test_mtf_endpoints():
    result = mtf(0.25, np.array([0.0, 1.0, 0.25]))
    assert result[0] == pytest.approx(0.0)
    assert result[1] == pytest.approx(1.0)
    assert result[2] == pytest.approx(0.5)


def test_auto_stretch_output_is_bounded_and_finite():
    rng = np.random.default_rng(0)
    data = rng.normal(loc=100.0, scale=15.0, size=(64, 64)).astype(np.float32)
    data = np.clip(data, 0, None)

    stretched = auto_stretch(data)

    assert stretched.shape == data.shape
    assert np.all(np.isfinite(stretched))
    assert stretched.min() >= 0.0
    assert stretched.max() <= 1.0


def test_auto_stretch_maps_background_near_target():
    """A synthetic sky-background-dominated image (most pixels near a low
    background level, a few bright 'stars') should land with its median
    close to PixInsight's default target_background of 0.25 after stretch,
    since that's exactly what the algorithm solves for."""
    rng = np.random.default_rng(1)
    background = rng.normal(loc=1000.0, scale=20.0, size=(128, 128)).astype(np.float32)
    background[:5, :5] += 20000  # a handful of bright "stars"

    stretched = auto_stretch(background)

    assert np.median(stretched) == pytest.approx(0.25, abs=0.02)


def test_auto_stretch_handles_all_zero_image():
    data = np.zeros((16, 16), dtype=np.float32)

    stretched = auto_stretch(data)

    assert np.all(stretched == 0.0)


def test_auto_stretch_handles_rgb_without_distorting_channel_ratios():
    """RGB linked stretch applies the SAME c0/m to every channel, so a pixel
    that is exactly gray (R==G==B) before the stretch must stay exactly
    gray after it."""
    rng = np.random.default_rng(2)
    gray_value = rng.normal(loc=500.0, scale=10.0, size=(32, 32)).astype(np.float32)
    data = np.stack([gray_value, gray_value, gray_value], axis=-1)

    stretched = auto_stretch(data)

    assert np.allclose(stretched[..., 0], stretched[..., 1])
    assert np.allclose(stretched[..., 1], stretched[..., 2])


def test_stretch_params_uses_full_array_max_not_subsample():
    """max_val must reflect the single brightest pixel even though
    median/avg_dev come from a deterministic subsample -- otherwise a rare
    hot pixel outside the subsample could be silently clipped."""
    data = np.full((50, 50), 100.0, dtype=np.float32)
    data[0, 0] = 65535.0  # one hot pixel, easily missed by subsampling

    _, _, max_val = _stretch_params(data)

    assert max_val == pytest.approx(65535.0)


def test_downsample_linear_leaves_small_images_unchanged():
    data = np.zeros((100, 200), dtype=np.float32)

    result = downsample_linear(data, max_size=2000)

    assert result is data


def test_downsample_linear_shrinks_to_longest_side():
    data = np.zeros((4176, 6248), dtype=np.float32)

    result = downsample_linear(data, max_size=2000)

    assert max(result.shape) == 2000
    assert result.dtype == np.float32
    # aspect ratio preserved
    assert result.shape[0] / result.shape[1] == pytest.approx(4176 / 6248, rel=0.01)


def test_downsample_linear_preserves_pixel_values_for_a_flat_image():
    data = np.full((4176, 6248), 42.0, dtype=np.float32)

    result = downsample_linear(data, max_size=2000)

    assert np.allclose(result, 42.0)


def test_downsample_linear_handles_rgb():
    data = np.zeros((4176, 6248, 3), dtype=np.float32)
    data[..., 0] = 10.0
    data[..., 1] = 20.0
    data[..., 2] = 30.0

    result = downsample_linear(data, max_size=2000)

    assert result.shape[-1] == 3
    assert max(result.shape[:2]) == 2000
    assert np.allclose(result[..., 0], 10.0)
    assert np.allclose(result[..., 1], 20.0)
    assert np.allclose(result[..., 2], 30.0)
