"""Post-stack processing: preset resolution (with config overrides), the
postprocess stage on stacked masters (FakeBackend standing in for
PixInsight/BlurXTerminator), and processed masters in the client zip."""
from __future__ import annotations

import json
import zipfile

import pytest
from astropy.io import fits

from skystack import jobs
from skystack import pipeline as P
from skystack.postprocess import PRESETS, output_name, resolve_steps
from tests.backends.fake_backend import FakeBackend
from tests.test_pipeline import TARGET, _decide, tree  # noqa: F401  (fixture)


def test_bxt_preset_matches_the_requested_setup():
    (step,) = resolve_steps({}, ["bxt_correct"])
    assert step["process"] == "BlurXTerminator"
    assert step["params"] == {
        "ai_file": "BlurXTerminator.4.pb", "correct_only": True, "correct_first": False,
        "nonstellar_then_stellar": False, "lum_only": False, "sharpen_stars": 0.20,
        "adjust_halos": 0.00, "nonstellar_psf_diameter": 0.00, "auto_nonstellar_psf": True,
        "sharpen_nonstellar": 0.80,
    }


def test_config_overrides_merge_params_and_can_add_presets():
    config = {"postprocess": {
        "default_steps": ["bxt_correct", "bxt_full"],
        "presets": {
            "bxt_correct": {"params": {"sharpen_stars": 0.5}},
            "bxt_full": {"label": "BXT full", "process": "BlurXTerminator", "suffix": "bxt",
                         "params": {"correct_only": False}},
        },
    }}

    steps = resolve_steps(config)

    assert [s["name"] for s in steps] == ["bxt_correct", "bxt_full"]
    assert steps[0]["params"]["sharpen_stars"] == 0.5
    assert steps[0]["params"]["sharpen_nonstellar"] == 0.80          # untouched keys survive
    assert PRESETS["bxt_correct"]["params"]["sharpen_stars"] == 0.20  # built-in not mutated
    assert output_name("masterLight_L", steps) == "masterLight_L_bxtc_bxt.xisf"
    with pytest.raises(ValueError, match="Unknown post-processing step"):
        resolve_steps(config, ["nope"])


def _stacked(config, paths, tmp_path):
    reference = P.calibrated_frames(paths)[0]
    P.stage_register(config, TARGET, reference=reference, backend=FakeBackend())
    tri = P.stage_triage_send(config, TARGET, deliver=False)
    P.stage_apply_triage(config, TARGET, [_decide(tmp_path, tri["session_id"], [])])
    P.stage_stack(config, TARGET, backend=FakeBackend())
    return P.stacked_masters(paths)


def test_postprocess_writes_processed_copies_and_export_includes_them(tree, tmp_path):
    config, paths, *_ = tree
    (master,) = _stacked(config, paths, tmp_path)
    before = master.read_bytes()

    results = P.stage_postprocess(config, TARGET, backend=FakeBackend())

    out = paths.processed_dir / f"{master.stem}_bxtc.xisf"
    assert results == [{"input": str(master), "status": "ok", "output": str(out)}]
    assert fits.getheader(out)["POSTPROC"] == "bxt_correct"
    assert master.read_bytes() == before
    assert P.load_state(paths)["postprocess"]["steps"] == ["bxt_correct"]

    names = zipfile.ZipFile(P.stage_export(config, TARGET)).namelist()
    assert f"{TARGET}/masters/{master.name}" in names
    assert f"{TARGET}/processed/{out.name}" in names


def test_postprocess_can_target_specific_masters(tree, tmp_path):
    config, paths, *_ = tree
    _stacked(config, paths, tmp_path)
    with pytest.raises(FileNotFoundError):
        P.stage_postprocess(config, TARGET, masters=["masterLight_missing.xisf"], backend=FakeBackend())


def test_postprocess_argv():
    assert jobs.pipeline_argv("postprocess", "M 31", ["s1"], steps=["bxt_correct"], masters=["a.xisf"]) == [
        "postprocess", "-t", "M 31", "--step", "bxt_correct", "--master", "a.xisf"]
    argv = jobs.pipeline_argv("finish", "M 31", decisions=["d.json"], postprocess=True, steps=["bxt_correct"])
    assert argv == ["finish", "-t", "M 31", "--postprocess", "--step", "bxt_correct", "d.json"]


def test_export_can_leave_processed_masters_out(tree, tmp_path):
    config, paths, *_ = tree
    _stacked(config, paths, tmp_path)
    P.stage_postprocess(config, TARGET, backend=FakeBackend())

    names = zipfile.ZipFile(P.stage_export(config, TARGET, include_processed=False)).namelist()

    assert not any("/processed/" in n for n in names)
    assert P.load_state(paths)["export"]["n_processed"] == 0


def test_processed_masters_can_be_added_to_an_existing_zip_post_hoc(tree, tmp_path):
    config, paths, *_ = tree
    (master,) = _stacked(config, paths, tmp_path)
    out = P.stage_export(config, TARGET, include_raw_lights="kept", include_processed=False)
    before = set(zipfile.ZipFile(out).namelist())
    with pytest.raises(RuntimeError, match="run 'postprocess' first"):
        P.stage_add_processed(config, TARGET)

    P.stage_postprocess(config, TARGET, backend=FakeBackend())
    info = P.stage_add_processed(config, TARGET)

    processed_name = f"{TARGET}/processed/{master.stem}_bxtc.xisf"
    assert info == {"zip": str(out), "added": [processed_name]}
    after = zipfile.ZipFile(out).namelist()
    assert set(after) == before | {processed_name}      # raw lights etc. untouched
    assert P.load_state(paths)["export"]["n_processed"] == 1

    # re-running (e.g. after re-processing) replaces instead of duplicating
    P.stage_add_processed(config, TARGET)
    names = zipfile.ZipFile(out).namelist()
    assert names.count(processed_name) == 1 and set(names) == set(after)
    assert zipfile.ZipFile(out).testzip() is None


def test_add_processed_argv():
    assert jobs.pipeline_argv("add-processed", "M 31", ["s1"]) == ["add-processed", "-t", "M 31"]
    assert jobs.pipeline_argv("export", "M 31", processed=False)[-1] == "--no-processed"
