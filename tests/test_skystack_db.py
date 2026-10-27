"""Tests for skystack_db.py: the target-status database and its
filesystem/JSON-based aggregate scan.

Directory trees are built by hand in tmp_path to mirror the real on-disk
conventions found in the user's actual data (LIGHT/<target>/<night>/..,
BLINKED/ folders, CALIBRATED/<target>/calibration_log.json,
REGISTERED/<target>/{registered,master}/), so these tests don't depend on
any real astrophotography data being present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skystack_db import (
    CalibrationFrame,
    LightFrame,
    MasterCalibration,
    ProcessType,
    SkyStackDatabase,
    TargetInfo,
    compute_target_stats,
)


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


@pytest.fixture
def data_tree(tmp_path):
    """Builds a LIGHT/CALIBRATED/REGISTERED tree for one target, "M1",
    with 3 nights: one fully blinked (5 frames moved to BLINKED), one
    still raw/unreviewed (3 frames), and one fully reviewed-and-rejected
    (2 frames moved to REJECTED)."""
    light_dir = tmp_path / "LIGHT"
    calibrated_dir = tmp_path / "CALIBRATED"
    registered_dir = tmp_path / "REGISTERED"

    # Night 1: fully blinked (moved out of LIGHT/H/..., none left behind)
    for i in range(5):
        _touch(light_dir / "M1" / "2025-01-01" / "BLINKED" / f"light_{i}.fits")

    # Night 2: still raw, unreviewed
    for i in range(3):
        _touch(
            light_dir / "M1" / "2025-01-02" / "LIGHT" / "H" / "600s_gain100"
            / f"light_{i}.fits"
        )

    # Night 3: reviewed and rejected (e.g. via QuickBlink's apply --discard-dir)
    for i in range(2):
        _touch(light_dir / "M1" / "2025-01-03" / "REJECTED" / f"light_{i}.fits")

    # Calibration outputs + log for 3 of the blinked frames: 1 still awaiting
    # calibrated-stage review, 2 already reviewed and kept (BLINKED) -- 3
    # total either way, so calibrated_frames stays 3.
    _touch(calibrated_dir / "M1" / "light_0__01012025_c.fits")
    for i in range(1, 3):
        _touch(calibrated_dir / "M1" / "BLINKED" / f"light_{i}__01012025_c.fits")
    log = {
        "Target name": "M1",
        "01-01-2025": {
            "H": {
                "600.0": {
                    "flat_master": str(tmp_path / "MASTERS" / "FLAT" / "masterFlat_H.fits"),
                    "dark_master": str(tmp_path / "MASTERS" / "DARK" / "masterDark_600s.fits"),
                    "num calibrated": 3,
                    "failed lights": [],
                }
            }
        },
    }
    (calibrated_dir / "M1" / "calibration_log.json").parent.mkdir(parents=True, exist_ok=True)
    (calibrated_dir / "M1" / "calibration_log.json").write_text(json.dumps(log))

    # Registration outputs: 3 registered frames + 1 final master
    for i in range(3):
        _touch(
            registered_dir / "M1" / "registered" / "Light_H_group"
            / f"light_{i}_r.fits"
        )
    _touch(registered_dir / "M1" / "master" / "masterLight_FILTER-H.xisf")
    # a non-master file in the same folder should not be counted
    _touch(registered_dir / "M1" / "master" / "LN_Reference_Light_FILTER-H.xisf")

    return {
        "light_base_dir": light_dir,
        "calibrated_base_dir": calibrated_dir,
        "registered_base_dir": registered_dir,
    }


def test_compute_target_stats_counts_lights_and_nights(data_tree):
    stats = compute_target_stats("M1", **data_tree)

    assert stats["total_lights"] == 10  # 5 blinked + 3 unreviewed + 2 rejected
    assert stats["nights"] == 3


def test_compute_target_stats_blink_yield(data_tree):
    stats = compute_target_stats("M1", **data_tree)

    assert stats["blinked_lights"] == 5
    assert stats["rejected_lights"] == 2
    assert stats["unreviewed_lights"] == 3
    # True yield among reviewed frames only: 5 / (5 + 2), NOT diluted by the
    # 3 still-unreviewed frames in night 2.
    assert stats["blink_yield_pct"] == pytest.approx(71.4)


def test_compute_target_stats_yield_is_none_with_nothing_reviewed_yet(tmp_path):
    light_dir = tmp_path / "LIGHT"
    for i in range(3):
        _touch(light_dir / "M2" / "2025-02-01" / "LIGHT" / "H" / f"light_{i}.fits")

    stats = compute_target_stats("M2", light_dir, tmp_path / "CALIBRATED", tmp_path / "REGISTERED")

    assert stats["blink_yield_pct"] is None
    assert stats["unreviewed_lights"] == 3


def test_compute_target_stats_calibration(data_tree):
    stats = compute_target_stats("M1", **data_tree)

    assert stats["calibrated_frames"] == 3
    assert stats["calibration_frames_used"] == 2  # one flat + one dark


def test_compute_target_stats_true_yield_uses_raw_light_count(data_tree):
    """The preferred workflow: calibrate everything, then triage the
    CALIBRATED frames. true_yield_pct must be measured against the RAW
    light count (10, from the fixture), not the calibrated-frame count --
    that's the whole point of this metric over the older blink_yield_pct."""
    stats = compute_target_stats("M1", **data_tree)

    assert stats["calibrated_blinked_frames"] == 2
    assert stats["true_yield_pct"] == pytest.approx(20.0)  # 2 / 10, not 2 / 3


def test_compute_target_stats_true_yield_is_none_without_any_raw_lights(tmp_path):
    calibrated_dir = tmp_path / "CALIBRATED"
    _touch(calibrated_dir / "M9" / "BLINKED" / "light_0__01012025_c.fits")

    stats = compute_target_stats("M9", tmp_path / "LIGHT", calibrated_dir, tmp_path / "REGISTERED")

    assert stats["true_yield_pct"] is None


def test_compute_target_stats_registration(data_tree):
    stats = compute_target_stats("M1", **data_tree)

    assert stats["registered_frames"] == 3
    assert stats["stacked_masters"] == 1  # LN_Reference_Light must not count


def test_compute_target_stats_missing_target_returns_zeros(data_tree):
    stats = compute_target_stats("NoSuchTarget", **data_tree)

    assert stats["total_lights"] == 0
    assert stats["nights"] == 0
    assert stats["blink_yield_pct"] is None


def test_compute_target_stats_malformed_log_does_not_raise(data_tree):
    (data_tree["calibrated_base_dir"] / "M1" / "calibration_log.json").write_text("{not json")

    stats = compute_target_stats("M1", **data_tree)

    assert stats["calibration_frames_used"] == 0
    assert stats["calibrated_frames"] == 3  # disk count is unaffected


# --- Database ---


@pytest.fixture
def db(tmp_path):
    database = SkyStackDatabase(tmp_path / "test.db")
    database.connect()
    yield database
    database.close()


def test_initialize_database_creates_all_tables(db):
    cursor = db.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}

    assert {
        "targets", "light_frames", "calibration_frames",
        "master_calibrations", "processing_logs",
    } <= tables


def test_upsert_and_refresh_round_trip(db, data_tree):
    n = db.refresh_all_targets(**data_tree)

    assert n == 1
    target = db.get_target_by_name("M1")
    assert target is not None
    assert target.total_lights == 10
    assert target.blinked_lights == 5
    assert target.rejected_lights == 2
    assert target.calibrated_blinked_frames == 2
    assert target.true_yield_pct == pytest.approx(20.0)
    assert target.registered_frames == 3
    assert target.stacked_masters == 1


def test_refresh_all_targets_upserts_not_duplicates(db, data_tree):
    db.refresh_all_targets(**data_tree)
    db.refresh_all_targets(**data_tree)

    assert len(db.get_all_targets()) == 1


def test_get_all_targets_includes_new_target_added_on_disk(db, data_tree):
    _touch(data_tree["light_base_dir"] / "M51" / "2025-02-01" / "light_0.fits")

    db.refresh_all_targets(**data_tree)

    names = {t.name for t in db.get_all_targets()}
    assert names == {"M1", "M51"}


def test_add_and_get_light_frame_round_trip(db):
    target_id = db.add_target(TargetInfo(name="M1"))
    frame_id = db.add_light_frame(
        LightFrame(target_id=target_id, local_file_path="C:/x/light_0.fits", filter_name="H")
    )

    frames = db.get_light_frames(target_id)

    assert len(frames) == 1
    assert frames[0].id == frame_id
    assert frames[0].filter_name == "H"


def test_add_and_get_calibration_frame_round_trip(db):
    """Regression test: calibration_frames table used to not exist at all."""
    db.add_calibration_frame(
        CalibrationFrame(frame_type="FLAT", local_file_path="C:/x/flat_0.fits", filter_name="H")
    )

    frames = db.get_calibration_frames("FLAT")

    assert len(frames) == 1
    assert frames[0].filter_name == "H"


def test_add_master_calibration(db):
    master_id = db.add_master_calibration(
        MasterCalibration(frame_type="DARK", file_path="C:/x/masterDark.fits", gain=100, offset=8)
    )

    assert master_id > 0


def test_processing_log_start_and_complete(db):
    target_id = db.add_target(TargetInfo(name="M1"))
    log_id = db.start_processing(target_id, ProcessType.CALIBRATION)

    db.complete_processing(log_id, status="completed", output_files=["a.fits"])

    cursor = db.conn.cursor()
    cursor.execute("SELECT status, output_files FROM processing_logs WHERE id = ?", (log_id,))
    row = cursor.fetchone()
    assert row["status"] == "completed"
    assert json.loads(row["output_files"]) == ["a.fits"]
