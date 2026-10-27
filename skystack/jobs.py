"""Background jobs for running run_routine.py from the web dashboard.

A pipeline stage can run for hours (calibration, registration, stacking),
far longer than a Streamlit button callback should block -- a page reload
or closed browser tab would orphan it. So the dashboard starts each run as
a detached background process and only ever reads its status back:

    pipeline_jobs/<job_id>.json   command, pid, status, exit code, times
    pipeline_jobs/<job_id>.log    everything run_routine.py printed

The detached process is this module itself (`python -m skystack.jobs
_exec <job_id>`), which runs run_routine.py with output going to the log
and writes the exit code into the job file when it finishes. A job whose
file still says "running" but whose process is gone is reported as
"died" (e.g. the machine rebooted mid-run).

Only one job runs at a time: every PixInsight-driven stage hands work to
PixInsight through the same C:/Temp/PixStack control files, so two
concurrent runs would overwrite each other's instructions. Jobs asked for
while another runs -- or by remote collaborators (skystack/remote_access.py)
-- wait as requests:

    queued              starts when nothing else runs (and, for a
                        collaborator's request, inside the access window)
    awaiting_approval   a collaborator's request the owner hasn't approved yet

start_next_queued() starts the oldest startable request; skystack_server.py
calls it periodically, and the dashboard after approving or queueing one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ROUTINE = REPO_ROOT / "run_routine.py"
DEFAULT_JOBS_DIR = REPO_ROOT / "pipeline_jobs"
STARTUP_GRACE_S = 60
WAITING_STATUSES = ("queued", "awaiting_approval")
REQUESTED_BY_ENV = "SKYSTACK_REQUESTED_BY"   # passed to the job, e.g. for web triage follow-ups

# Stages the dashboard can launch, in pipeline order
STAGES = ("run", "calibrate", "measure", "reference", "register", "triage",
          "apply-triage", "stack", "postprocess", "export", "add-processed", "finish")
TRIAGE_MODES = ("local", "web", "send", "build")
# stages that carry on after triage decisions: stack, then [postprocess] and [export]
_AFTER_TRIAGE = ("run", "triage", "finish")


# ---------------------------------------------------------------------------
# Building the run_routine.py command line
# ---------------------------------------------------------------------------

def pipeline_argv(
    stage: str,
    target: str,
    sessions: Sequence[str] = (),
    *,
    skip_transfer: bool = False,
    skip_masters: bool = False,
    skip_measure: bool = False,
    triage_mode: Optional[str] = None,
    local: bool = False,
    web: bool = False,
    carry_on: bool = True,
    triage_scope: str = "pending",
    deliver: bool = True,
    canvas: Optional[str] = None,
    reference_frame: Optional[str] = None,
    force: bool = False,
    decisions: Sequence[Path] = (),
    export: Optional[bool] = None,
    raw_lights: Optional[str] = None,
    with_calibration: Optional[bool] = None,
    postprocess: Optional[bool] = None,
    steps: Sequence[str] = (),
    masters: Sequence[str] = (),
    processed: Optional[bool] = None,
) -> List[str]:
    """Arguments for run_routine.py; options that don't apply to `stage` are ignored."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}")
    if stage in ("apply-triage", "finish") and not decisions:
        raise ValueError(f"'{stage}' needs at least one decisions file")
    if triage_mode is not None and triage_mode not in TRIAGE_MODES:
        raise ValueError(f"Unknown triage mode {triage_mode!r}")

    argv = [stage, "-t", target]
    if stage not in ("apply-triage", "stack", "postprocess", "add-processed"):
        for s in sessions:
            argv += ["-s", s]

    if stage == "run":
        argv += ["--skip-transfer"] if skip_transfer else []
        argv += ["--skip-masters"] if skip_masters else []
        argv += ["--skip-measure"] if skip_measure else []
        if triage_mode:
            argv += ["--triage", triage_mode]
        elif not deliver:
            argv.append("--no-deliver")
    if stage == "triage":
        argv += ["--all"] if triage_scope == "all" else []
        if local or web:
            argv.append("--local" if local else "--web")
            argv += ["--no-continue"] if not carry_on else []
        elif not deliver:
            argv.append("--no-deliver")
    if stage in ("run", "reference"):
        if canvas:
            argv += ["--canvas", canvas]
        if reference_frame:
            argv += ["--reference-frame", reference_frame]
    if stage in ("register", "measure") and force:
        argv.append("--force")
    if stage in _AFTER_TRIAGE:
        if postprocess is not None:
            argv.append("--postprocess" if postprocess else "--no-postprocess")
        if export is not None:
            argv.append("--export" if export else "--no-export")
    if stage in _AFTER_TRIAGE or stage == "postprocess":
        for s in steps:
            argv += ["--step", s]
    if stage in _AFTER_TRIAGE or stage == "export":
        if raw_lights:
            argv += ["--raw-lights", raw_lights]
        if with_calibration is not None:
            argv.append("--with-calibration" if with_calibration else "--no-calibration")
        if processed is not None:
            argv.append("--processed" if processed else "--no-processed")
    if stage == "postprocess":
        for m in masters:
            argv += ["--master", m]
    if stage in ("apply-triage", "finish"):
        argv += [str(d) for d in decisions]
    return argv


# ---------------------------------------------------------------------------
# Job files
# ---------------------------------------------------------------------------

def _job_path(jobs_dir: Path, job_id: str) -> Path:
    return Path(jobs_dir) / f"{job_id}.json"


def _write_job(jobs_dir: Path, job: Dict[str, Any]) -> None:
    path = _job_path(jobs_dir, job["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, indent=2))
    tmp.replace(path)  # atomic, so the dashboard never reads half a file


def read_job(job_id: str, jobs_dir: Path = DEFAULT_JOBS_DIR) -> Dict[str, Any]:
    job = json.loads(_job_path(jobs_dir, job_id).read_text())
    if job["status"] == "running" and job.get("pid") is None:
        # runner hasn't recorded its pid yet; give it a moment to start
        age = (datetime.now() - datetime.fromisoformat(job["started"])).total_seconds()
        if age > STARTUP_GRACE_S:
            job["status"] = "died"
        return job
    if job["status"] == "running" and not _process_alive(job.get("pid"), job.get("pid_create_time")):
        # Re-read once: the runner may have just finished and written its result.
        job = json.loads(_job_path(jobs_dir, job_id).read_text())
        if job["status"] == "running":
            job["status"] = "died"
    return job


def list_jobs(jobs_dir: Path = DEFAULT_JOBS_DIR, target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Jobs newest first, optionally only one target's."""
    jobs_dir = Path(jobs_dir)
    if not jobs_dir.exists():
        return []
    jobs = []
    for p in jobs_dir.glob("*.json"):
        try:
            job = read_job(p.stem, jobs_dir)
        except (json.JSONDecodeError, OSError, KeyError):
            continue
        if target is None or job.get("target") == target:
            jobs.append(job)
    return sorted(jobs, key=_job_time, reverse=True)


def _job_time(job: Dict[str, Any]) -> str:
    return job.get("started") or job.get("submitted") or ""


def running_job(jobs_dir: Path = DEFAULT_JOBS_DIR) -> Optional[Dict[str, Any]]:
    return next((j for j in list_jobs(jobs_dir) if j["status"] == "running"), None)


def waiting_jobs(jobs_dir: Path = DEFAULT_JOBS_DIR) -> List[Dict[str, Any]]:
    """Queued and awaiting-approval requests, oldest first."""
    return sorted((j for j in list_jobs(jobs_dir) if j["status"] in WAITING_STATUSES), key=_job_time)


def read_log(job: Dict[str, Any], max_lines: int = 200) -> str:
    if not job.get("log"):
        return ""
    log = Path(job["log"])
    if not log.exists():
        return ""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


# ---------------------------------------------------------------------------
# Starting / stopping
# ---------------------------------------------------------------------------

def _process_alive(pid: Optional[int], create_time: Optional[float]) -> bool:
    if not pid:
        return False
    import psutil
    try:
        proc = psutil.Process(pid)
        # guard against Windows reusing the pid for an unrelated process
        return create_time is None or abs(proc.create_time() - create_time) < 1.0
    except psutil.NoSuchProcess:
        return False


def _new_job(argv: Sequence[str], jobs_dir: Path, status: str,
             requested_by: Optional[str] = None) -> Dict[str, Any]:
    argv = list(argv)
    now = datetime.now()
    # time-ordered, plus a random part: two requests in the same clock tick mustn't collide
    job_id = now.strftime("%Y%m%d-%H%M%S-%f") + "-" + os.urandom(2).hex()
    return {
        "id": job_id,
        "argv": argv,
        "stage": argv[0] if argv else None,
        "target": argv[argv.index("-t") + 1] if "-t" in argv else None,
        "status": status,
        "returncode": None,
        "submitted": now.isoformat(timespec="seconds"),
        "started": None,
        "ended": None,
        "log": str(Path(jobs_dir) / f"{job_id}.log"),
        "pid": None,
        "pid_create_time": None,
        "requested_by": requested_by,
    }


def _launch(job: Dict[str, Any], jobs_dir: Path) -> Dict[str, Any]:
    job.update(status="running", started=datetime.now().isoformat(timespec="seconds"))
    _write_job(jobs_dir, job)

    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        # keep running if the dashboard (and its console) goes away
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    # The runner records its own pid when it starts (see _exec), so this
    # process never writes the job file again -- no race with a runner that
    # finishes instantly.
    subprocess.Popen(
        [sys.executable, "-m", "skystack.jobs", "_exec", job["id"], str(jobs_dir)],
        cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True, **kwargs,
    )
    return job


def start_job(argv: Sequence[str], jobs_dir: Path = DEFAULT_JOBS_DIR,
              requested_by: Optional[str] = None) -> Dict[str, Any]:
    """Launch run_routine.py <argv> in the background. Refuses if a job is running."""
    jobs_dir = Path(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    current = running_job(jobs_dir)
    if current is not None:
        raise RuntimeError(f"A pipeline job is already running: {' '.join(current['argv'])}")
    return _launch(_new_job(argv, jobs_dir, "running", requested_by), jobs_dir)


def submit_request(argv: Sequence[str], jobs_dir: Path = DEFAULT_JOBS_DIR,
                   requested_by: Optional[str] = None, needs_approval: bool = False) -> Dict[str, Any]:
    """Queue run_routine.py <argv> to start when it may (see start_next_queued)."""
    jobs_dir = Path(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job = _new_job(argv, jobs_dir, "awaiting_approval" if needs_approval else "queued", requested_by)
    _write_job(jobs_dir, job)
    return job


def _set_waiting_status(job_id: str, jobs_dir: Path, expect: Sequence[str], **changes) -> Dict[str, Any]:
    job = read_job(job_id, jobs_dir)
    if job["status"] not in expect:
        raise RuntimeError(f"Request {job_id} is {job['status']}, not {' or '.join(expect)}.")
    job.update(changes)
    _write_job(jobs_dir, job)
    return job


def approve_request(job_id: str, jobs_dir: Path = DEFAULT_JOBS_DIR, by: Optional[str] = None) -> Dict[str, Any]:
    return _set_waiting_status(job_id, jobs_dir, ("awaiting_approval",), status="queued", approved_by=by)


def reject_request(job_id: str, jobs_dir: Path = DEFAULT_JOBS_DIR, by: Optional[str] = None) -> Dict[str, Any]:
    return _set_waiting_status(job_id, jobs_dir, WAITING_STATUSES, status="rejected", rejected_by=by,
                               ended=datetime.now().isoformat(timespec="seconds"))


def start_next_queued(jobs_dir: Path = DEFAULT_JOBS_DIR,
                      may_start: Callable[[Dict[str, Any]], bool] = lambda job: True) -> Optional[Dict[str, Any]]:
    """Start the oldest queued request that may_start(job) allows, if nothing is running."""
    jobs_dir = Path(jobs_dir)
    if running_job(jobs_dir) is not None:
        return None
    for job in waiting_jobs(jobs_dir):
        if job["status"] == "queued" and may_start(job):
            return _launch(job, jobs_dir)
    return None


def cancel_job(job_id: str, jobs_dir: Path = DEFAULT_JOBS_DIR) -> None:
    """Kill the job and everything it started (including PixInsight)."""
    import psutil

    job = read_job(job_id, jobs_dir)
    if job["status"] in WAITING_STATUSES:
        job.update(status="cancelled", ended=datetime.now().isoformat(timespec="seconds"))
        _write_job(jobs_dir, job)
        return
    if job["status"] == "running" and job.get("pid") is None:
        # psutil.Process(None) would mean *this* process -- never do that
        raise RuntimeError("The job is still starting; try cancelling again in a moment.")
    if job["status"] == "running":
        try:
            root = psutil.Process(job["pid"])
            procs = root.children(recursive=True) + [root]
        except psutil.NoSuchProcess:
            procs = []
        for p in procs:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(procs, timeout=10)
    job = json.loads(_job_path(jobs_dir, job_id).read_text())
    if job["status"] == "running":
        job.update(status="cancelled", ended=datetime.now().isoformat(timespec="seconds"))
        _write_job(jobs_dir, job)


def _exec(job_id: str, jobs_dir: Path) -> int:
    """Body of the detached runner process."""
    import psutil

    job = json.loads(_job_path(jobs_dir, job_id).read_text())
    me = psutil.Process()
    job.update(pid=me.pid, pid_create_time=me.create_time())
    _write_job(jobs_dir, job)
    with open(job["log"], "w", encoding="utf-8", buffering=1) as log:
        log.write(f"$ python run_routine.py {' '.join(job['argv'])}\n\n")
        log.flush()
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        if job.get("requested_by"):
            env[REQUESTED_BY_ENV] = job["requested_by"]
        try:
            rc = subprocess.run(
                [sys.executable, "-u", str(RUN_ROUTINE), *job["argv"]],
                cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
            ).returncode
        except Exception as e:  # e.g. interpreter missing -- still record an outcome
            log.write(f"\nFailed to start: {e}\n")
            rc = -1
    job = json.loads(_job_path(jobs_dir, job_id).read_text())
    if job["status"] == "running":  # cancel_job may have got here first
        job.update(status="succeeded" if rc == 0 else "failed", returncode=rc,
                   ended=datetime.now().isoformat(timespec="seconds"))
        _write_job(jobs_dir, job)
    return rc


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "_exec":
        sys.exit(_exec(sys.argv[2], Path(sys.argv[3])))
    sys.exit("usage: python -m skystack.jobs _exec <job_id> <jobs_dir>")
