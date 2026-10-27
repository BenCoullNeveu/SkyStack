"""Tests for the existing astro_utils/pixinsight_cli.py free functions.

These validate step 2's starting point: the PixInsight adapter these tests
exercise is exactly what the new PixInsightBackend wrapper will delegate to
(or replace). They avoid ever spawning a real PixInsight process by
monkeypatching `_run_cmd`, but they do use the module's real fixed sentinel
directory (C:/Temp/PixStack) since the sentinel paths are hardcoded string
literals inside the functions rather than module-level constants -- one of
the things worth fixing when this logic moves into PixInsightBackend.

Marked pixinsight_legacy: do not run while a real PixInsight automation job
is in progress, since these tests read/write files in its control directory.
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

import astro_utils.pixinsight_cli as pix

pytestmark = pytest.mark.pixinsight_legacy


def test_save_json_creates_parent_dirs_and_writes_json(tmp_path):
    target = tmp_path / "nested" / "data.json"

    pix.save_json({"a": 1}, target)

    assert target.exists()
    assert json.loads(target.read_text()) == {"a": 1}


def test_stack_with_pixinsight_writes_json_and_builds_expected_command(
    monkeypatch, tmp_path, pixinsight_control_dir, synthetic_fits_file
):
    monkeypatch.setattr(pix, "INPUT_FILES_JSON", tmp_path / "input_files.json")
    monkeypatch.setattr(pix, "PARAMS_JSON", tmp_path / "params.json")

    sentinel = pixinsight_control_dir / "basic_stack_complete.tmp"
    sentinel.unlink(missing_ok=True)  # in case a previous crashed run left one

    captured = {}

    def fake_run_cmd(cmd_list):
        captured["cmd"] = cmd_list
        sentinel.touch()  # simulate PixInsight finishing and signalling completion
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pix, "_run_cmd", fake_run_cmd)

    files = [synthetic_fits_file(), synthetic_fits_file()]
    out_path = tmp_path / "master.xisf"

    pix.stack_with_pixinsight(files, out_path, instance=3)

    assert not sentinel.exists()  # function must clean up its own sentinel

    cmd = captured["cmd"]
    assert cmd[0] == pix.PIXINSIGHT_PATH
    assert "--automation-mode" in cmd
    assert any(arg.startswith("-x=3:") for arg in cmd)

    saved_inputs = json.loads((tmp_path / "input_files.json").read_text())
    assert len(saved_inputs) == 2

    saved_params = json.loads((tmp_path / "params.json").read_text())
    assert saved_params["output_path"] == str(out_path.absolute())


def test_calibrate_with_pixinsight_writes_empty_string_for_missing_masters(
    monkeypatch, tmp_path, pixinsight_control_dir, synthetic_fits_file
):
    monkeypatch.setattr(pix, "INPUT_FILES_JSON", tmp_path / "input_files.json")
    monkeypatch.setattr(pix, "PARAMS_JSON", tmp_path / "params.json")

    sentinel = pixinsight_control_dir / "calibration_complete.tmp"
    sentinel.unlink(missing_ok=True)

    def fake_run_cmd(cmd_list):
        sentinel.touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pix, "_run_cmd", fake_run_cmd)

    lights = [synthetic_fits_file()]

    pix.calibrate_with_pixinsight(
        lights, None, None, tmp_path / "out", date(2025, 10, 8), instance=2
    )

    params = json.loads((tmp_path / "params.json").read_text())
    assert params["master_flat"] == ""
    assert params["master_dark"] == ""
    assert params["prefix"] == "__08102025_c"


def test_stack_with_pixinsight_raises_on_failed_subprocess_call(
    monkeypatch, tmp_path, pixinsight_control_dir, synthetic_fits_file
):
    """Regression test for a fixed bug: stack_with_pixinsight used to never
    check the subprocess return code before polling for the completion
    sentinel, so a stale sentinel left over from an earlier crashed run made
    a brand-new failing call look like a silent success. It now clears any
    stale sentinel up front and raises immediately on a non-zero exit code.
    """
    monkeypatch.setattr(pix, "INPUT_FILES_JSON", tmp_path / "input_files.json")
    monkeypatch.setattr(pix, "PARAMS_JSON", tmp_path / "params.json")

    sentinel = pixinsight_control_dir / "basic_stack_complete.tmp"
    sentinel.touch()  # simulate a leftover sentinel from a previous crashed run

    def failing_run_cmd(cmd_list):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(pix, "_run_cmd", failing_run_cmd)

    files = [synthetic_fits_file(), synthetic_fits_file()]

    with pytest.raises(RuntimeError, match="exit 1"):
        pix.stack_with_pixinsight(files, tmp_path / "master.xisf", instance=3)

    # the stale sentinel must have been cleared, not left for the next call
    assert not sentinel.exists()
