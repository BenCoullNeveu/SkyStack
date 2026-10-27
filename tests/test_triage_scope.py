"""Which registered frames a triage page shows: frames waiting for triage
(this pipeline's flat registered/ or WBPP's registered/<group>/ folders), or
every registered frame (scope "all") to redo a triage -- and that re-applying
decisions leaves frames already in the right place alone."""
from __future__ import annotations

import json

import pytest

from skystack import jobs
from skystack import pipeline as P
from tests.test_pipeline import TARGET, _decide, _write, tree  # noqa: F401  (fixture)

STEM = "LIGHT_L_M 31_300.00s_0.00C_gain0_off8_rot65.21_{i:04d}__16092025_c_r"


@pytest.fixture
def wbpp_target(tree):
    """A target triaged and stacked with WBPP + QuickBlink: group folders,
    LN reference data, a group BLINKED/ folder -- plus pipeline BLINKED/ and REJECTED/."""
    config, paths, *_ = tree
    group = paths.registered_dir / "Light_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-L_mono"
    for i in range(3):
        _write(group / f"{STEM.format(i=i)}.xisf", FILTER="L", EXPTIME=300.0)
    (group / f"{STEM.format(i=0)}.xnml").write_text("not an image")
    _write(group / "ln_reference_frame_data" / "LN_Reference_c_r.xisf")
    _write(group / "BLINKED" / f"{STEM.format(i=3)}.xisf", FILTER="L", EXPTIME=300.0)
    _write(paths.blinked_dir / f"{STEM.format(i=4)}.xisf", FILTER="L", EXPTIME=300.0)
    _write(paths.rejected_dir / f"{STEM.format(i=5)}.xisf", FILTER="L", EXPTIME=300.0)
    # the same raw frame both waiting in a group folder and already kept: BLINKED/ wins
    _write(paths.blinked_dir / f"{STEM.format(i=2)}.xisf", FILTER="L", EXPTIME=300.0)
    return config, paths


def _names(frames):
    return sorted(f"{f.parent.name}/{f.name}" for f in frames)


def test_pending_finds_frames_in_wbpp_group_folders(wbpp_target):
    config, paths = wbpp_target
    group = "Light_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-L_mono"
    assert _names(P.pending_registered_frames(paths)) == [f"{group}/{STEM.format(i=i)}.xisf" for i in range(3)]


def test_all_scope_adds_already_triaged_frames_once_each(wbpp_target):
    config, paths = wbpp_target
    frames = P.triage_candidates(paths, "all")
    by_stem = {P.raw_stem(f): f.parent.name for f in frames}

    assert len(frames) == len(by_stem) == 6
    assert by_stem[STEM.format(i=2).rsplit("__", 1)[0]] == "BLINKED"     # kept copy preferred
    assert by_stem[STEM.format(i=3).rsplit("__", 1)[0]] == "BLINKED"     # WBPP group BLINKED/
    assert by_stem[STEM.format(i=5).rsplit("__", 1)[0]] == "REJECTED"
    with pytest.raises(ValueError):
        P.triage_candidates(paths, "everything")


def test_retriage_moves_only_frames_whose_decision_changed(wbpp_target, tmp_path):
    config, paths = wbpp_target
    tri = P.stage_triage_send(config, TARGET, deliver=False, scope="all")
    manifest = json.loads(open(tri["manifest"]).read())
    assert tri["scope"] == "all" and tri["n_frames"] == 6
    keys = list(manifest["frames"])
    to_reject = next(k for k in keys if k.startswith("BLINKED/") and STEM.format(i=2) in k)
    # everything else is kept -- including the frame that was in REJECTED/ before

    P.stage_apply_triage(config, TARGET, [_decide(tmp_path, tri["session_id"], [to_reject])])

    blinked = sorted(p.name for p in paths.blinked_dir.iterdir())
    rejected = sorted(p.name for p in paths.rejected_dir.iterdir())
    assert f"{STEM.format(i=4)}.xisf" in blinked                       # untouched, not renamed
    assert not any("_copy" in n for n in blinked + rejected)
    assert f"{STEM.format(i=2)}.xisf" in rejected                      # kept -> rejected
    assert f"{STEM.format(i=5)}.xisf" in blinked                       # rejected -> kept
    assert len(P.triage_candidates(paths, "all")) == 6                  # nothing lost or duplicated


def test_triage_all_argv():
    assert jobs.pipeline_argv("triage", "M 31", local=True, triage_scope="all") == [
        "triage", "-t", "M 31", "--all", "--local"]
