"""End-to-end test of skystack/pipeline.py stages register -> triage ->
apply-triage -> stack -> export, on a synthetic directory tree with the
FakeBackend standing in for PixInsight (identity registration, mean stack).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from skystack import pipeline as P
from skystack.client_export import calibration_masters_for, raw_stem_of
from skystack.triage import DECISIONS_FORMAT
from tests.backends.fake_backend import FakeBackend

TARGET = "M 31"


def _write(path: Path, value=100.0, **header):
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.full((20, 30), value, dtype=np.float32))
    for k, v in header.items():
        hdu.header[k] = v
    hdu.writeto(path)
    return path


@pytest.fixture
def tree(tmp_path):
    """Raw lights in two sessions, their calibrated versions, a calibration log."""
    light, cal, reg = tmp_path / "LIGHT", tmp_path / "CALIBRATED", tmp_path / "REGISTERED"
    flat = _write(tmp_path / "MASTERS" / "FLAT" / "masterFlat_L.xisf")
    dark = _write(tmp_path / "MASTERS" / "DARK" / "masterDark_300s.xisf")
    sessions = {"2025-01-01": "2025-01-02T02:00:00", "2025-01-05": "2025-01-06T02:00:00"}
    for session, date_obs in sessions.items():
        for i in range(4):
            stem = f"LIGHT_L_{TARGET}_300.00s_{session}_{i:04d}"
            hdr = dict(FILTER="L", EXPTIME=300.0, **{"DATE-OBS": date_obs})
            _write(light / TARGET / session / "LIGHT" / "L" / f"{stem}.fits", **hdr)
            ddmmyyyy = date_obs[8:10] + date_obs[5:7] + date_obs[:4]
            _write(cal / TARGET / f"{stem}__{ddmmyyyy}_c.fits", value=100.0 + i, **hdr)
    log = {"Target name": TARGET}
    for date_obs in sessions.values():
        log[f"{date_obs[8:10]}-{date_obs[5:7]}-{date_obs[:4]}"] = {
            "L": {"300.0": {"flat_master": str(flat), "dark_master": str(dark), "num calibrated": 4}}
        }
    (cal / TARGET / "calibration_log.json").write_text(json.dumps(log))

    config = {
        "light_base_dir": str(light), "calibrated_output_dir": str(cal),
        "registered_output_dir": str(reg),
        "remote_triage": {"preview_size": 32, "workers": 1},
        "client_export": {"client_targets": [TARGET], "output_dir": str(tmp_path / "CLIENT"),
                          "master_format": "xisf"},
    }
    return config, P.TargetPaths.from_config(config, TARGET), flat, dark


def _decide(tmp_path, session_id, discarded):
    p = tmp_path / "decisions.json"
    p.write_text(json.dumps({"format": DECISIONS_FORMAT, "session_id": session_id,
                             "reviewer": "t", "discarded": discarded}))
    return p


def test_raw_stem_strips_calibration_and_registration_suffixes():
    assert P.raw_stem(Path("LIGHT_L_0001__01012025_c_r.xisf")) == "LIGHT_L_0001"
    assert raw_stem_of(Path("LIGHT_L_0001__01012025_c.xisf")) == "LIGHT_L_0001"


def test_calibrated_frames_can_be_limited_to_a_session(tree):
    config, paths, *_ = tree
    assert len(P.calibrated_frames(paths)) == 8
    only = P.calibrated_frames(paths, ["2025-01-05"])
    assert len(only) == 4 and all("2025-01-05" in f.name for f in only)
    with pytest.raises(FileNotFoundError):
        P.calibrated_frames(paths, ["1999-01-01"])


@pytest.mark.parametrize("n, expected", [(3, "PercentileClip"), (8, "WinsorizedSigmaClip"),
                                         (19, "WinsorizedSigmaClip"), (20, "LinearFit")])
def test_rejection_by_frame_count(n, expected):
    assert P.choose_rejection(n) == expected


def test_reference_stage_honours_a_user_selected_frame(tree):
    config, paths, *_ = tree
    frames = P.calibrated_frames(paths)
    ref = P.stage_reference(config, TARGET, reference_frame=frames[2].name)
    assert ref == frames[2]
    assert P.load_state(paths)["reference"]["mode"] == "frame"


def test_full_flow_register_triage_stack_export(tree, tmp_path):
    config, paths, flat, dark = tree
    reference = P.calibrated_frames(paths)[0]

    reg = P.stage_register(config, TARGET, reference=reference, backend=FakeBackend())
    assert reg["registered"] == 8 and reg["failed"] == []
    # second run registers nothing new
    assert P.stage_register(config, TARGET, reference=reference, backend=FakeBackend())["registered"] == 0

    tri = P.stage_triage_send(config, TARGET, deliver=False)
    assert Path(tri["package"]).exists() and tri["n_frames"] == 8

    pending = P.pending_registered_frames(paths)
    rejected = [p.relative_to(paths.root).as_posix() for p in (pending[0], pending[5])]   # page keys
    summary = P.stage_apply_triage(config, TARGET, [_decide(tmp_path, tri["session_id"], rejected)])
    assert (summary["kept"], summary["rejected"]) == (6, 2)
    assert len(list(paths.blinked_dir.iterdir())) == 6
    assert len(list(paths.rejected_dir.iterdir())) == 2
    assert P.pending_registered_frames(paths) == []
    assert P.load_state(paths)["triage"]["applied"] is True

    results = P.stage_stack(config, TARGET, backend=FakeBackend())
    assert [r["status"] for r in results] == ["ok"]
    assert results[0]["n_frames"] == 6 and results[0]["rejection"] == "PercentileClip"
    master = paths.master_dir / "masterLight_BIN-1_EXPOSURE-300.00s_FILTER-L.xisf"
    assert master.exists()

    assert P.is_client_target(config, TARGET)
    out = P.stage_export(config, TARGET, include_raw_lights="kept", include_calibration_masters=True)
    names = zipfile.ZipFile(out).namelist()
    assert f"{TARGET}/masters/{master.name}" in names
    assert f"{TARGET}/integration_summary.csv" in names
    assert sum(n.startswith(f"{TARGET}/raw_lights/") for n in names) == 6
    assert f"{TARGET}/calibration_masters/flats/{flat.name}" in names
    assert f"{TARGET}/calibration_masters/darks/{dark.name}" in names


def test_restacking_keeps_the_previous_master(tree, tmp_path):
    config, paths, *_ = tree
    P.stage_register(config, TARGET, reference=P.calibrated_frames(paths)[0], backend=FakeBackend())
    tri = P.stage_triage_send(config, TARGET, deliver=False)
    P.stage_apply_triage(config, TARGET, [_decide(tmp_path, tri["session_id"], [])])

    P.stage_stack(config, TARGET, backend=FakeBackend())
    P.stage_stack(config, TARGET, backend=FakeBackend())
    assert len(list((paths.master_dir / "superseded").iterdir())) == 1


def test_small_groups_are_skipped_not_failed(tree, tmp_path):
    config, paths, *_ = tree
    config = {**config, "stacking": {"min_frames": 50}}
    P.stage_register(config, TARGET, reference=P.calibrated_frames(paths)[0], backend=FakeBackend())
    tri = P.stage_triage_send(config, TARGET, deliver=False)
    P.stage_apply_triage(config, TARGET, [_decide(tmp_path, tri["session_id"], [])])
    assert [r["status"] for r in P.stage_stack(config, TARGET, backend=FakeBackend())] == ["skipped"]


def test_calibration_masters_only_for_matching_dates(tree):
    config, paths, flat, dark = tree
    raws = sorted((paths.light_dir / "2025-01-05").rglob("*.fits"))
    assert calibration_masters_for(raws, paths.calibrated_dir / "calibration_log.json") == {flat, dark}
    assert calibration_masters_for(raws, paths.calibrated_dir / "missing.json") == set()
