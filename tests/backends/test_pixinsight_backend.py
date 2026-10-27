"""Unit tests for skystack.backends.pixinsight.PixInsightBackend.

These monkeypatch the astro_utils.pixinsight_cli functions the backend
delegates to, so they run without a real PixInsight install. See
tests/test_backend_contract.py for the protocol-conformance suite that DOES
exercise a real PixInsight instance (opt-in via SKYSTACK_PIXINSIGHT_TESTS=1).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import skystack.backends.pixinsight as pix_backend


@pytest.fixture(autouse=True)
def no_gpu_probe(monkeypatch):
    """Don't query the real machine's GPU / running PixInsight from unit tests."""
    monkeypatch.setattr(pix_backend, "decide_gpu", lambda mode, min_free: (True, "test"))


def test_negative_instance_means_reuse_existing_and_skip_launch(monkeypatch):
    launched = []
    monkeypatch.setattr(
        pix_backend, "launch_pixinsight", lambda instance, **kw: launched.append(instance)
    )

    backend = pix_backend.PixInsightBackend({"pixinsight_instance": -1})
    backend.start()

    assert launched == []
    assert backend._instance is None


def test_positive_instance_is_launched_and_reused_for_calls(monkeypatch):
    launched = []
    monkeypatch.setattr(
        pix_backend,
        "launch_pixinsight",
        lambda instance, **kw: launched.append(instance) or "proc",
    )

    backend = pix_backend.PixInsightBackend({"pixinsight_instance": 3})
    backend.start()
    backend.start()  # idempotent -- must not launch a second instance

    assert launched == [3]
    assert backend._instance == 3


def test_stop_closes_process_when_exit_instance_true(monkeypatch):
    monkeypatch.setattr(pix_backend, "launch_pixinsight", lambda instance, **kw: "proc")
    closed = []
    monkeypatch.setattr(pix_backend, "close_pixinsight", lambda proc: closed.append(proc))

    backend = pix_backend.PixInsightBackend({"pixinsight_instance": 1, "exit_instance": True})
    backend.start()
    backend.stop()

    assert closed == ["proc"]


def test_stop_does_not_close_process_when_exit_instance_false(monkeypatch):
    monkeypatch.setattr(pix_backend, "launch_pixinsight", lambda instance, **kw: "proc")
    closed = []
    monkeypatch.setattr(pix_backend, "close_pixinsight", lambda proc: closed.append(proc))

    backend = pix_backend.PixInsightBackend(
        {"pixinsight_instance": 1, "exit_instance": False}
    )
    backend.start()
    backend.stop()

    assert closed == []


def test_stop_without_start_does_not_raise(monkeypatch):
    def fail_if_called(proc):
        raise AssertionError("close_pixinsight should not be called")

    monkeypatch.setattr(pix_backend, "close_pixinsight", fail_if_called)

    backend = pix_backend.PixInsightBackend({"pixinsight_instance": 1})
    backend.stop()


def test_stack_raises_if_output_was_not_produced(monkeypatch, tmp_path):
    monkeypatch.setattr(pix_backend, "stack_with_pixinsight", lambda *a, **k: None)
    backend = pix_backend.PixInsightBackend({})

    with pytest.raises(RuntimeError):
        backend.stack([tmp_path / "a.fits"], tmp_path / "master.xisf")


def test_stack_returns_output_path_when_produced(monkeypatch, tmp_path):
    out = tmp_path / "master.xisf"

    def fake_stack(files, output_path, instance=None, timeout_s=None):
        Path(output_path).write_bytes(b"")

    monkeypatch.setattr(pix_backend, "stack_with_pixinsight", fake_stack)
    backend = pix_backend.PixInsightBackend({})

    result = backend.stack([tmp_path / "a.fits"], out)

    assert result == out


def test_calibrate_finds_produced_outputs_by_naming_convention(monkeypatch, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    light = tmp_path / "light_0001.fits"
    light.write_bytes(b"")

    def fake_calibrate(lights, flat, dark, output_dir, date_obs, instance=None, timeout_s=None):
        stem = Path(lights[0]).stem
        suffix = date_obs.strftime("%d%m%Y")
        (Path(output_dir) / f"{stem}__{suffix}_c.fits").write_bytes(b"")

    monkeypatch.setattr(pix_backend, "calibrate_with_pixinsight", fake_calibrate)
    backend = pix_backend.PixInsightBackend({})

    outputs = backend.calibrate([light], None, None, out_dir, date_obs=date(2025, 10, 8))

    assert len(outputs) == 1
    assert outputs[0].name == "light_0001__08102025_c.fits"


def test_calibrate_skips_lights_with_no_produced_output(monkeypatch, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    lights = [tmp_path / "ok.fits", tmp_path / "failed.fits"]
    for light in lights:
        light.write_bytes(b"")

    def fake_calibrate(lights, flat, dark, output_dir, date_obs, instance=None, timeout_s=None):
        suffix = date_obs.strftime("%d%m%Y")
        (Path(output_dir) / f"ok__{suffix}_c.fits").write_bytes(b"")
        # "failed.fits" intentionally produces no output

    monkeypatch.setattr(pix_backend, "calibrate_with_pixinsight", fake_calibrate)
    backend = pix_backend.PixInsightBackend({})

    outputs = backend.calibrate(lights, None, None, out_dir, date_obs=date(2025, 10, 8))

    assert len(outputs) == 1
    assert outputs[0].name == "ok__08102025_c.fits"


def test_launch_without_gpu_when_another_instance_holds_it(monkeypatch):
    launched = []
    monkeypatch.setattr(pix_backend, "decide_gpu", lambda mode, min_free: (False, "busy"))
    monkeypatch.setattr(pix_backend, "launch_pixinsight",
                        lambda instance, use_gpu: launched.append(use_gpu) or "proc")

    pix_backend.PixInsightBackend({"pixinsight_instance": 2}).start()

    assert launched == [False]


@pytest.mark.parametrize("others, free, expected", [
    ([], 100, True),            # nobody else on the GPU
    ([84836], 5000, True),      # plenty left
    ([84836], 377, False),      # the stall case: interactive PixInsight holds the GPU
    ([84836], None, True),      # can't tell -> don't take the GPU away
])
def test_decide_gpu_auto(monkeypatch, others, free, expected):
    import astro_utils.pixinsight_cli as cli

    monkeypatch.setattr(cli, "other_pixinsight_pids", lambda: others)
    monkeypatch.setattr(cli, "gpu_free_mb", lambda: free)

    assert cli.decide_gpu("auto", 2048)[0] is expected
    assert cli.decide_gpu("on")[0] is True and cli.decide_gpu("off")[0] is False
