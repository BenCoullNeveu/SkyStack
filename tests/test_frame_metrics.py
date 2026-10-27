"""Frame quality metrics and the triage tool's subframe selector: the measure
stage (FakeBackend standing in for PixInsight's SubframeSelector) right after
calibration, metrics embedded in the triage page, decisions carrying
metric-cut rejects, and the local review server that takes them directly."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

import pytest

from skystack import jobs
from skystack import pipeline as P
from tests.backends.fake_backend import FakeBackend
from tests.test_pipeline import TARGET, _decide, tree  # noqa: F401  (fixture)


def _with_fake_star_metrics(paths, fwhm_by_index):
    """Set FAKEFWHM on calibrated frames by their _000N index."""
    from astropy.io import fits

    for f in P.calibrated_frames(paths):
        i = int(P.raw_stem(f)[-1])
        with fits.open(f, mode="update") as hdul:
            hdul[0].header["FAKEFWHM"] = fwhm_by_index.get(i, 2.0)


def _page_data(html_path):
    html = open(html_path, encoding="utf-8").read()
    return json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1))


def test_measure_stores_metrics_once_per_frame(tree):
    config, paths, *_ = tree
    _with_fake_star_metrics(paths, {3: 4.5})

    info = P.stage_measure(config, TARGET, backend=FakeBackend())

    assert info["measured"] == 8 and info["failed"] == []
    with P._open_db() as db:
        df = db.frame_metrics_frame(TARGET)
    assert len(df) == 8 and set(df["filter_name"]) == {"L"} and set(df["exposure_time"]) == {300.0}
    assert sorted(df["fwhm"])[-2:] == [4.5, 4.5]
    # already-measured frames are skipped unless forced
    assert P.stage_measure(config, TARGET, backend=FakeBackend())["measured"] == 0
    assert P.stage_measure(config, TARGET, ["2025-01-01"], force=True, backend=FakeBackend())["measured"] == 4


def _measured_and_registered(config, paths):
    """New order: measure the calibrated frames, then reference + register."""
    _with_fake_star_metrics(paths, {3: 4.5})
    P.stage_measure(config, TARGET, backend=FakeBackend())
    P.stage_reference(config, TARGET, reference_frame=P.calibrated_frames(paths)[0].name)
    P.stage_register(config, TARGET, backend=FakeBackend())


def test_triage_page_carries_metrics_of_the_registered_frames(tree):
    config, paths, *_ = tree
    _measured_and_registered(config, paths)

    tri = P.stage_triage_send(config, TARGET, deliver=False)
    data = _page_data(tri["package"])

    assert tri["n_measured"] == 8 and data["local"] is None
    assert {m["key"] for m in data["metric_spec"]} >= {"fwhm", "eccentricity", "stars", "psf_signal_weight"}
    fwhm = {f["name"]: f["metrics"]["fwhm"] for f in data["frames"]}
    assert sorted(fwhm.values()) == [2.0] * 6 + [4.5] * 2


def test_metric_cut_rejects_are_applied_and_recorded(tree, tmp_path):
    config, paths, *_ = tree
    _measured_and_registered(config, paths)
    tri = P.stage_triage_send(config, TARGET, deliver=False)
    page = _page_data(tri["package"])["frames"]
    by_cut = [f["key"] for f in page if f["metrics"]["fwhm"] > 3.0]   # what a FWHM > 3 cut rejects
    by_eye = next(f["key"] for f in page if f["key"] not in by_cut)
    p = tmp_path / "d.json"
    p.write_text(json.dumps({
        "format": "SkyStack triage decisions", "version": 2, "session_id": tri["session_id"],
        "reviewer": "me", "discarded": sorted(by_cut + [by_eye]), "metric_rejected": by_cut,
        "metric_cuts": {"L · 300s": {"fwhm": 3.0}},
    }))

    summary = P.stage_apply_triage(config, TARGET, [p])

    assert (summary["kept"], summary["rejected"], summary["metric_rejected"]) == (5, 3, 2)
    state = P.load_state(paths)["triage"]
    assert state["metric_rejected"] == 2 and state["metric_cuts"][0]["cuts"] == {"L · 300s": {"fwhm": 3.0}}
    assert len(list(paths.rejected_dir.iterdir())) == 3


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_local_review_serves_the_page_and_takes_decisions(tree, monkeypatch):
    config, paths, *_ = tree
    _measured_and_registered(config, paths)
    opened = []
    monkeypatch.setattr("skystack.local_review.open_app_window", lambda url, browser="auto": opened.append(url))
    result = {}
    t = threading.Thread(target=lambda: result.update(
        out=P.stage_triage_local(config, TARGET, next_steps="stacking", timeout_s=30)))
    t.start()
    for _ in range(200):
        if opened:
            break
        time.sleep(0.05)
    url = opened[0]

    html = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
    assert '"submit_url":"' + url + '/submit"' in html and '"next_steps":"stacking"' in html
    session_id = P.load_state(paths)["triage"]["session_id"]
    with pytest.raises(urllib.error.HTTPError) as err:   # other URLs (wrong token) are refused
        urllib.request.urlopen(url.rsplit("/", 1)[0] + "/wrong-token", timeout=10)
    assert err.value.code == 404
    code, reply = _post(url + "/submit", {"format": "SkyStack triage decisions", "session_id": "other"})
    assert code == 400 and not reply["ok"]
    code, reply = _post(url + "/submit", {"format": "SkyStack triage decisions", "session_id": session_id,
                                          "discarded": [], "total": 8, "kept_count": 8})
    assert code == 200 and reply["ok"]
    t.join(10)

    saved = json.loads(result["out"].read_text())
    assert saved["session_id"] == session_id and result["out"].parent == paths.triage_dir / "decisions"
    assert P.load_state(paths)["triage"]["mode"] == "local"
    # the decisions file is ready for the rest of the pipeline
    assert P.stage_apply_triage(config, TARGET, [result["out"]])["kept"] == 8


def test_new_run_and_triage_argv():
    assert jobs.pipeline_argv("measure", "M 31", ["s1"], force=True) == ["measure", "-t", "M 31", "-s", "s1", "--force"]
    assert jobs.pipeline_argv("run", "M 31", skip_measure=True, triage_mode="local", postprocess=True,
                              steps=["bxt_correct"], export=False) == [
        "run", "-t", "M 31", "--skip-measure", "--triage", "local", "--postprocess", "--no-export",
        "--step", "bxt_correct"]
    assert jobs.pipeline_argv("triage", "M 31", local=True, carry_on=False) == [
        "triage", "-t", "M 31", "--local", "--no-continue"]
    assert jobs.pipeline_argv("triage", "M 31", deliver=False) == ["triage", "-t", "M 31", "--no-deliver"]
    with pytest.raises(ValueError):
        jobs.pipeline_argv("run", "M 31", triage_mode="later")
