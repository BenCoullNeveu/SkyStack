"""Tests for skystack/jobs.py -- background run_routine.py jobs for the dashboard."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

import psutil
import pytest

from skystack import jobs


# --- command lines ------------------------------------------------------------

def test_run_argv_with_every_option():
    argv = jobs.pipeline_argv("run", "M 31", ["2024-12-05", "2024-12-08"], skip_transfer=True,
                              deliver=False, canvas="intersection", reference_frame="a_c.xisf")
    assert argv == ["run", "-t", "M 31", "-s", "2024-12-05", "-s", "2024-12-08", "--skip-transfer",
                    "--no-deliver", "--canvas", "intersection", "--reference-frame", "a_c.xisf"]


def test_options_that_do_not_apply_to_a_stage_are_dropped():
    argv = jobs.pipeline_argv("stack", "M 31", ["2024-12-05"], canvas="union", force=True,
                              raw_lights="all", deliver=False)
    assert argv == ["stack", "-t", "M 31"]


def test_finish_argv_carries_export_choices_and_decisions(tmp_path):
    d = tmp_path / "d.json"
    argv = jobs.pipeline_argv("finish", "M 31", decisions=[d], export=False,
                              raw_lights="kept", with_calibration=False)
    assert argv == ["finish", "-t", "M 31", "--no-export", "--raw-lights", "kept",
                    "--no-calibration", str(d)]


def test_argv_is_accepted_by_run_routines_parser(tmp_path):
    import run_routine

    for argv in (
        jobs.pipeline_argv("run", "M 31", ["s1"], skip_masters=True, canvas="union"),
        jobs.pipeline_argv("finish", "M 31", decisions=[tmp_path / "d.json"], export=True,
                           raw_lights="all", with_calibration=True),
        jobs.pipeline_argv("export", "M 31", raw_lights="none", with_calibration=False),
        jobs.pipeline_argv("register", "M 31", force=True),
    ):
        run_routine.build_parser().parse_args(argv)  # raises SystemExit if not


def test_finish_without_decisions_is_refused():
    with pytest.raises(ValueError):
        jobs.pipeline_argv("finish", "M 31")
    with pytest.raises(ValueError):
        jobs.pipeline_argv("deploy", "M 31")


# --- job lifecycle --------------------------------------------------------------

def _fake_job(jobs_dir, **overrides):
    jobs_dir.mkdir(exist_ok=True)
    job = {"id": "j1", "argv": ["stack", "-t", "M 31"], "stage": "stack", "target": "M 31",
           "status": "running", "returncode": None, "ended": None,
           "started": datetime.now().isoformat(timespec="seconds"),
           "log": str(jobs_dir / "j1.log"), "pid": None, "pid_create_time": None, **overrides}
    (jobs_dir / "j1.json").write_text(json.dumps(job))
    return job


def test_running_job_whose_process_is_gone_reports_died(tmp_path):
    _fake_job(tmp_path / "jobs", pid=2**22 + 12345, pid_create_time=1.0)
    assert jobs.read_job("j1", tmp_path / "jobs")["status"] == "died"


def test_job_still_starting_is_not_reported_dead_until_the_grace_period(tmp_path):
    _fake_job(tmp_path / "jobs")
    assert jobs.read_job("j1", tmp_path / "jobs")["status"] == "running"
    old = (datetime.now() - timedelta(seconds=jobs.STARTUP_GRACE_S + 5)).isoformat(timespec="seconds")
    _fake_job(tmp_path / "jobs", started=old)
    assert jobs.read_job("j1", tmp_path / "jobs")["status"] == "died"


def test_only_one_job_at_a_time(tmp_path):
    me = psutil.Process()
    _fake_job(tmp_path / "jobs", pid=me.pid, pid_create_time=me.create_time())
    with pytest.raises(RuntimeError, match="already running"):
        jobs.start_job(["stack", "-t", "M 31"], tmp_path / "jobs")


def test_cancel_never_kills_the_dashboard_while_a_job_is_starting(tmp_path):
    _fake_job(tmp_path / "jobs")  # no pid recorded yet
    with pytest.raises(RuntimeError, match="still starting"):
        jobs.cancel_job("j1", tmp_path / "jobs")
    assert psutil.Process().is_running()


def test_exec_records_exit_code_and_output(tmp_path, monkeypatch):
    script = tmp_path / "fake_routine.py"
    script.write_text("import sys; print('stage output', sys.argv[1:]); sys.exit(3)")
    monkeypatch.setattr(jobs, "RUN_ROUTINE", script)
    _fake_job(tmp_path / "jobs")

    assert jobs._exec("j1", tmp_path / "jobs") == 3
    job = jobs.read_job("j1", tmp_path / "jobs")
    assert job["status"] == "failed" and job["returncode"] == 3 and job["pid"] == psutil.Process().pid
    assert "stage output ['stack', '-t', 'M 31']" in jobs.read_log(job)


def test_detached_job_runs_to_completion(tmp_path):
    """Real end-to-end launch: `run_routine.py status` only reads state."""
    job = jobs.start_job(["status", "-t", "__no_such_target__"], tmp_path / "jobs")
    deadline = time.monotonic() + 60
    while jobs.read_job(job["id"], tmp_path / "jobs")["status"] == "running":
        assert time.monotonic() < deadline, "job never finished"
        time.sleep(0.5)
    done = jobs.read_job(job["id"], tmp_path / "jobs")
    assert done["status"] == "succeeded", jobs.read_log(done)
    assert '"target": "__no_such_target__"' in jobs.read_log(done)
    assert [j["id"] for j in jobs.list_jobs(tmp_path / "jobs", target="__no_such_target__")] == [job["id"]]
