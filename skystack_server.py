"""Run SkyStack as a small server, for remote collaborators over Tailscale.

    python skystack_server.py

Starts, all bound to 127.0.0.1 only:
  - the dashboard (Streamlit) on remote_access.dashboard_port (8501)
  - the web triage server on remote_access.triage_port (8502)
  - the job queue: every few seconds, starts the next queued request that
    may run now (collaborators' requests only inside the access windows)
and, with remote_access.manage_tailscale_serve, publishes them with
`tailscale serve` while remote access is open and withdraws them when it
closes. Without it, publish once by hand (see README, "Remote access").

Run it from your logged-in Windows session (e.g. a Task Scheduler task "At
log on"), not as a service: PixInsight needs the desktop. Ctrl+C stops it.
"""
import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from astro_utils.config_loader import load_config
from skystack import jobs
from skystack.remote_access import ACT_AS_ENV, AccessConfig, tailscale_serve
from skystack.triage_server import TriageServer

REPO_ROOT = Path(__file__).parent
TICK_S = 15

logging.basicConfig(level="INFO", format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("skystack_server")


def _access() -> AccessConfig:
    # re-read each tick, so edits to config.yaml (collaborators, windows) apply without a restart
    return AccessConfig.from_config(load_config(REPO_ROOT / "config.yaml"))


def _start_dashboard(port: int) -> subprocess.Popen:
    return subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "skystack_ui.py"),
        "--server.address", "127.0.0.1", "--server.port", str(port),
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ], cwd=REPO_ROOT)


def tick(access: AccessConfig) -> None:
    job = jobs.start_next_queued(may_start=access.may_start)
    if job:
        logger.info("Started queued job %s: %s (requested by %s)", job["id"], " ".join(job["argv"]),
                    job.get("requested_by") or "owner")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-tailscale", action="store_true",
                        help="Don't touch `tailscale serve`, even with manage_tailscale_serve")
    parser.add_argument("--act-as", metavar="LOGIN",
                        help="Testing: treat visits from this PC as this Tailscale login (e.g. a "
                             "collaborator's), to try their view, the window and approvals")
    args = parser.parse_args()
    if args.act_as:
        os.environ[ACT_AS_ENV] = args.act_as   # inherited by the dashboard process too
        logger.warning("TEST MODE: visits from this PC count as %s", args.act_as)

    access = _access()
    dashboard = _start_dashboard(access.dashboard_port)
    triage = TriageServer(access, on_submit=lambda job: tick(_access())).start()
    logger.info("Dashboard http://127.0.0.1:%d · web triage http://127.0.0.1:%d/triage/ · %s",
                access.dashboard_port, triage.port, access.status_text())
    served = None
    try:
        while True:
            access = _access()
            tick(access)
            if access.manage_tailscale_serve and not args.no_tailscale:
                want = access.enabled and access.is_open()
                if want != served:
                    logger.info("%s the dashboard on the tailnet (%s)", "Publishing" if want else "Withdrawing",
                                access.status_text())
                    tailscale_serve(want, access)
                    served = want
            if dashboard.poll() is not None:
                logger.warning("Dashboard exited (code %s); restarting", dashboard.returncode)
                dashboard = _start_dashboard(access.dashboard_port)
            time.sleep(TICK_S)
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        dashboard.terminate()
        triage.stop()
        if served:
            tailscale_serve(False, access)


if __name__ == "__main__":
    main()
