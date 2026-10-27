"""Tests for QuickBlink.py's apply_session() -- the step that moves a
reviewed session's keep/discard decisions back to the original files.

Builds sessions as plain directories with a session.json (load_session
supports this directly), rather than real .qblink zips or real FITS/WebP
files, since apply_session never reads image content.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from QuickBlink import _safe_move, apply_session, compute_crop_box, discover_images


def _make_session(tmp_path, source_dir: Path, records: list[dict]) -> Path:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    session = {
        "format": "QuickBlink",
        "version": 1,
        "source_directory": str(source_dir),
        "image_count": len(records),
        "images": records,
    }
    (session_dir / "session.json").write_text(json.dumps(session))
    return session_dir / "session.json"


def _record(name: str, discarded: bool = False) -> dict:
    return {
        "filename": name,
        "relative_path": name,
        "preview": f"previews/{name}.webp",
        "discarded": discarded,
    }


@pytest.fixture
def source_dir(tmp_path):
    d = tmp_path / "source"
    d.mkdir()
    for name in ("a.fits", "b.fits", "c.fits"):
        (d / name).write_bytes(b"")
    return d


def test_apply_moves_kept_files_to_keep_dir(tmp_path, source_dir):
    session_path = _make_session(
        tmp_path, source_dir,
        [_record("a.fits"), _record("b.fits", discarded=True)],
    )
    keep_dir = tmp_path / "keep"

    summary = apply_session(session_path, keep_dir=keep_dir)

    assert summary == {
        "kept": 1, "discarded": 1, "missing": 0,
        "moved_kept": 1, "moved_discarded": 0, "deleted": 0,
    }
    assert (keep_dir / "a.fits").exists()
    assert not (source_dir / "a.fits").exists()
    assert (source_dir / "b.fits").exists()  # discarded, left in place (no discard_dir given)


def test_apply_moves_discarded_files_to_discard_dir(tmp_path, source_dir):
    session_path = _make_session(
        tmp_path, source_dir,
        [_record("a.fits"), _record("b.fits", discarded=True)],
    )
    discard_dir = tmp_path / "rejected"

    summary = apply_session(session_path, discard_dir=discard_dir)

    assert summary["moved_discarded"] == 1
    assert (discard_dir / "b.fits").exists()
    assert (source_dir / "a.fits").exists()  # kept, left in place (no keep_dir given)


def test_apply_deletes_discarded_when_requested(tmp_path, source_dir):
    session_path = _make_session(tmp_path, source_dir, [_record("b.fits", discarded=True)])

    summary = apply_session(session_path, delete_discarded=True)

    assert summary["deleted"] == 1
    assert not (source_dir / "b.fits").exists()


def test_apply_rejects_both_discard_dir_and_delete_discarded(tmp_path, source_dir):
    session_path = _make_session(tmp_path, source_dir, [_record("b.fits", discarded=True)])

    with pytest.raises(ValueError):
        apply_session(session_path, discard_dir=tmp_path / "x", delete_discarded=True)


def test_apply_dry_run_moves_nothing_but_counts_correctly(tmp_path, source_dir):
    session_path = _make_session(
        tmp_path, source_dir,
        [_record("a.fits"), _record("b.fits", discarded=True)],
    )
    keep_dir = tmp_path / "keep"
    discard_dir = tmp_path / "rejected"

    summary = apply_session(session_path, keep_dir=keep_dir, discard_dir=discard_dir, dry_run=True)

    assert summary["moved_kept"] == 1
    assert summary["moved_discarded"] == 1
    assert not keep_dir.exists()
    assert not discard_dir.exists()
    assert (source_dir / "a.fits").exists()
    assert (source_dir / "b.fits").exists()


def test_apply_with_no_destinations_leaves_everything_in_place(tmp_path, source_dir):
    session_path = _make_session(
        tmp_path, source_dir,
        [_record("a.fits"), _record("b.fits", discarded=True)],
    )

    summary = apply_session(session_path)

    assert summary["moved_kept"] == 0
    assert summary["moved_discarded"] == 0
    assert summary["deleted"] == 0
    assert (source_dir / "a.fits").exists()
    assert (source_dir / "b.fits").exists()


def test_apply_counts_missing_files_without_raising(tmp_path, source_dir):
    session_path = _make_session(tmp_path, source_dir, [_record("does_not_exist.fits")])

    summary = apply_session(session_path, keep_dir=tmp_path / "keep")

    assert summary["missing"] == 1
    assert summary["moved_kept"] == 0


def test_safe_move_avoids_overwriting_on_name_collision(tmp_path):
    src1 = tmp_path / "a.fits"
    src1.write_bytes(b"first")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "a.fits").write_bytes(b"existing")

    result = _safe_move(src1, dest_dir / "a.fits")

    assert result == dest_dir / "a_copy1.fits"
    assert (dest_dir / "a.fits").read_bytes() == b"existing"
    assert result.read_bytes() == b"first"


def test_discover_images_excludes_already_reviewed_dirs_by_default(tmp_path):
    (tmp_path / "new_frame.fits").write_bytes(b"")
    (tmp_path / "BLINKED").mkdir()
    (tmp_path / "BLINKED" / "kept_frame.fits").write_bytes(b"")
    (tmp_path / "Rejected_MEASURED").mkdir()
    (tmp_path / "Rejected_MEASURED" / "bad_frame.fits").write_bytes(b"")

    found = discover_images(tmp_path)

    assert [p.name for p in found] == ["new_frame.fits"]


def test_discover_images_exclusion_is_overridable(tmp_path):
    (tmp_path / "BLINKED").mkdir()
    (tmp_path / "BLINKED" / "kept_frame.fits").write_bytes(b"")

    found = discover_images(tmp_path, exclude_dir_names=set())

    assert [p.name for p in found] == ["kept_frame.fits"]


# --- Zoom panel crop math (GUI-free: no Tkinter instance needed) ---


def test_compute_crop_box_centers_on_the_given_point():
    left, top, right, bottom = compute_crop_box((1000, 1000), (0.5, 0.5), 0.2)

    assert (right - left, bottom - top) == (200, 200)
    assert (left + right) / 2 == pytest.approx(500, abs=1)
    assert (top + bottom) / 2 == pytest.approx(500, abs=1)


def test_compute_crop_box_clamps_near_top_left_edge():
    left, top, right, bottom = compute_crop_box((1000, 1000), (0.0, 0.0), 0.2)

    assert left == 0
    assert top == 0
    assert (right - left, bottom - top) == (200, 200)


def test_compute_crop_box_clamps_near_bottom_right_edge():
    left, top, right, bottom = compute_crop_box((1000, 1000), (1.0, 1.0), 0.2)

    assert right == 1000
    assert bottom == 1000
    assert (right - left, bottom - top) == (200, 200)


def test_compute_crop_box_respects_non_square_images():
    left, top, right, bottom = compute_crop_box((2000, 500), (0.5, 0.5), 0.1)

    assert (right - left, bottom - top) == (200, 50)
