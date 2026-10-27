"""Tests for skystack.domain: FrameKeywordMap and FrameMeta.

These validate step 1 of the backend-generalization refactor: centralizing
the FITS/XISF header keyword mapping and typed metadata extraction that
today is duplicated across calibrate_images.py, stack_darks.py,
stack_flats.py, and stack_calibration_frames.py.
"""
from __future__ import annotations

import pytest

from astro_utils.fits_helpers import parse_date_from_header, read_image_header_info
from skystack.domain import FrameKeywordMap, FrameMeta

COMMON_HEADER = dict(FILTER="Ha", GAIN=100, OFFSET=10, EXPTIME=300.0, ROTATANG=1.5)


# --- FrameKeywordMap ---


def test_keyword_map_uses_config_overrides():
    km = FrameKeywordMap.from_config(
        {"header_filter_key": "FILT", "header_gain_key": "EGAIN"}
    )
    assert km.filter == "FILT"
    assert km.gain == "EGAIN"


def test_keyword_map_falls_back_to_defaults_for_unset_keys():
    km = FrameKeywordMap.from_config({"header_filter_key": "FILT"})
    assert km.filter == "FILT"
    assert km.rotation == "ROTATANG"
    assert km.temperature == "SET-TEMP"


# --- FrameMeta: pure logic, no files involved ---


def test_frame_meta_numeric_casting():
    km = FrameKeywordMap()
    header = {"GAIN": "100", "OFFSET": "10", "EXPTIME": "300.0", "ROTATANG": "1.5"}
    fm = FrameMeta.from_header_dict(header, km)
    assert fm.gain == 100 and isinstance(fm.gain, int)
    assert fm.offset == 10 and isinstance(fm.offset, int)
    assert fm.exptime == 300.0 and isinstance(fm.exptime, float)
    assert fm.rotation == 1.5


def test_frame_meta_missing_values_become_none():
    km = FrameKeywordMap()
    fm = FrameMeta.from_header_dict({}, km)
    assert fm.filter is None
    assert fm.gain is None
    assert fm.date_obs is None


def test_frame_meta_malformed_numeric_becomes_none_not_an_exception():
    km = FrameKeywordMap()
    fm = FrameMeta.from_header_dict({"GAIN": "not-a-number"}, km)
    assert fm.gain is None


@pytest.mark.parametrize(
    "date_str,expected_ymd",
    [
        ("2025-10-08T23:14:02", (2025, 10, 8)),
        ("2025-10-08T23:14:02.123456", (2025, 10, 8)),
    ],
)
def test_frame_meta_parses_both_date_formats(date_str, expected_ymd):
    km = FrameKeywordMap()
    fm = FrameMeta.from_header_dict({"DATE-LOC": date_str}, km)
    assert (fm.date_obs.year, fm.date_obs.month, fm.date_obs.day) == expected_ymd


def test_frame_meta_prefers_date_loc_over_date_obs():
    km = FrameKeywordMap()
    fm = FrameMeta.from_header_dict(
        {"DATE-LOC": "2025-01-01T00:00:00", "DATE-OBS": "2020-06-15T00:00:00"}, km
    )
    assert fm.date_obs.year == 2025


# --- Parity tests: new extraction must match the legacy path exactly ---


def test_frame_meta_matches_legacy_for_fits(synthetic_fits_file, repo_keymap):
    path = synthetic_fits_file(
        **COMMON_HEADER, **{"SET-TEMP": -10.0, "DATE-LOC": "2025-10-08T23:14:02"}
    )

    legacy = read_image_header_info(path, fmt="fits")
    legacy_date = parse_date_from_header(path, fmt="fits")

    fm = FrameMeta.from_file(path, repo_keymap, fmt="fits")

    assert fm.filter == legacy[repo_keymap.filter]
    assert fm.gain == legacy[repo_keymap.gain]
    assert fm.offset == legacy[repo_keymap.offset]
    assert fm.exptime == legacy[repo_keymap.exptime]
    assert fm.rotation == legacy[repo_keymap.rotation]
    assert fm.temperature == legacy[repo_keymap.temperature]
    assert fm.date_obs == legacy_date


def test_frame_meta_matches_legacy_for_xisf(synthetic_xisf_file, repo_keymap):
    path = synthetic_xisf_file(
        **COMMON_HEADER, **{"SET-TEMP": -10.0, "DATE-LOC": "2025-10-08T23:14:02"}
    )

    legacy = read_image_header_info(path, fmt="xisf")
    legacy_date = parse_date_from_header(path, fmt="xisf")

    fm = FrameMeta.from_file(path, repo_keymap, fmt="xisf")

    assert fm.filter == legacy[repo_keymap.filter]
    assert fm.gain == legacy[repo_keymap.gain]
    assert fm.offset == legacy[repo_keymap.offset]
    assert fm.exptime == legacy[repo_keymap.exptime]
    assert fm.rotation == legacy[repo_keymap.rotation]
    assert fm.temperature == legacy[repo_keymap.temperature]
    assert fm.date_obs == legacy_date


def test_frame_meta_agrees_across_fits_and_xisf(
    synthetic_fits_file, synthetic_xisf_file, repo_keymap
):
    """The same logical header written as FITS vs XISF must extract
    identically -- this is exactly what find_best_masterv2's format
    filtering silently depends on today with no test proving it."""
    header = dict(COMMON_HEADER, **{"SET-TEMP": -10.0})
    fits_path = synthetic_fits_file(**header)
    xisf_path = synthetic_xisf_file(**header)

    fm_fits = FrameMeta.from_file(fits_path, repo_keymap, fmt="fits")
    fm_xisf = FrameMeta.from_file(xisf_path, repo_keymap, fmt="xisf")

    assert fm_fits.filter == fm_xisf.filter
    assert fm_fits.gain == fm_xisf.gain
    assert fm_fits.offset == fm_xisf.offset
    assert fm_fits.exptime == fm_xisf.exptime
    assert fm_fits.rotation == fm_xisf.rotation
    assert fm_fits.temperature == fm_xisf.temperature
