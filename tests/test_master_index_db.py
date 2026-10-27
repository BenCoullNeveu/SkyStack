"""Master flat/dark metadata kept in skystack.db (replacing master_index.parquet):
the incremental sync against the master folders, and calibration's master
selection reading from it."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from skystack_db import SkyStackDatabase


def _write_master(path: Path, **header) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = fits.Header()
    for k, v in header.items():
        hdr[k] = v
    fits.PrimaryHDU(data=np.zeros((4, 4), dtype=np.float32), header=hdr).writeto(path, overwrite=True)
    return path


FLAT_HDR = dict(FILTER="L", GAIN=100, OFFSET=50, EXPTIME=2.0, ROTATANG=90.0)


@pytest.fixture
def db(tmp_path):
    database = SkyStackDatabase(tmp_path / "test.db")
    database.connect()
    yield database
    database.close()


def test_sync_adds_masters_with_header_metadata(db, tmp_path):
    flats = tmp_path / "MASTERS" / "FLAT"
    _write_master(flats / "masterFlat_L__05122024.fits", **FLAT_HDR, **{"DATE-OBS": "2024-12-05T21:00:00.000"})
    _write_master(flats / "USED" / "ignored__05122024.fits", **FLAT_HDR)  # subfolders aren't masters

    counts = db.sync_master_calibrations(flats, "FLAT")
    df = db.master_calibrations_frame("FLAT", flats)

    assert counts["added"] == 1
    assert list(df["path"]) == [str(flats / "masterFlat_L__05122024.fits")]
    row = df.iloc[0]
    assert (row["fmt"], row["filter"], row["gain"], row["offset"], row["rot"]) == ("fits", "L", 100, 50, 90.0)
    assert row["date"].date() == date(2024, 12, 5)


def test_sync_is_incremental_and_tracks_changes_and_deletions(db, tmp_path):
    flats = tmp_path / "FLAT"
    a = _write_master(flats / "masterFlat_L__05122024.fits", **FLAT_HDR)
    b = _write_master(flats / "masterFlat_L__06122024.fits", **FLAT_HDR)
    db.sync_master_calibrations(flats, "FLAT")

    assert db.sync_master_calibrations(flats, "FLAT") == {"added": 0, "updated": 0, "removed": 0, "failed": 0}

    _write_master(a, **dict(FLAT_HDR, FILTER="H"))
    os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 10))
    b.unlink()
    counts = db.sync_master_calibrations(flats, "FLAT")

    assert (counts["updated"], counts["removed"]) == (1, 1)
    df = db.master_calibrations_frame("FLAT")
    assert list(df["filter"]) == ["H"]


def test_sync_skips_masters_without_gain(db, tmp_path):
    flats = tmp_path / "FLAT"
    _write_master(flats / "masterFlat_L__05122024.fits", FILTER="L", EXPTIME=2.0)

    counts = db.sync_master_calibrations(flats, "FLAT")

    assert counts["failed"] == 1
    assert db.master_calibrations_frame("FLAT").empty


def test_sync_only_touches_its_own_folder(db, tmp_path):
    one, two = tmp_path / "A", tmp_path / "B"
    _write_master(one / "masterFlat_L__05122024.fits", **FLAT_HDR)
    _write_master(two / "masterFlat_L__06122024.fits", **FLAT_HDR)
    db.sync_master_calibrations(one, "FLAT")
    db.sync_master_calibrations(two, "FLAT")

    assert len(db.master_calibrations_frame("FLAT")) == 2
    assert len(db.master_calibrations_frame("FLAT", one)) == 1


def test_find_best_master_uses_db_frame(db, tmp_path):
    import calibrate_images as ci

    flats = tmp_path / "FLAT"
    _write_master(flats / "masterFlat_L__01122024.fits", **FLAT_HDR)
    near = _write_master(flats / "masterFlat_L__05122024.fits", **FLAT_HDR)
    _write_master(flats / "masterFlat_H__05122024.fits", **dict(FLAT_HDR, FILTER="H"))
    db.sync_master_calibrations(flats, "FLAT")

    light = {ci.HEADER_FILTER_KEY: "L", ci.HEADER_GAIN_KEY: 100, ci.HEADER_OFFSET_KEY: 50,
             ci.HEADER_ROTATION_KEY: 90.2}
    best = ci.find_best_masterv2(flats, light, date(2024, 12, 6), max_days=30,
                                 ignore_expt=True, ignore_temp=True, master_fmt="fits",
                                 masters=db.master_calibrations_frame("FLAT", flats))

    assert best == near
