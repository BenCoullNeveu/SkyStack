"""Web triage: the triage tool in a (remote) reviewer's own browser.

`run_routine.py triage --web` builds the usual triage page and registers it
here as a session (pipeline_jobs/web_triage/<session>.json) -- then the job
ends, so nobody holds the one-job-at-a-time lock while a review is open.
This server (started by skystack_server.py, behind `tailscale serve` at
/triage) serves the page at

    /triage/<session>/<token>/

and takes its "Finish review" POST at .../submit: it saves the decisions next
to the package and queues the follow-up job (apply decisions -> stack ->
[postprocess] -> [export], or just apply) as the same person's request.
Visitors are checked the same way as the dashboard (skystack/remote_access.py).
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime
from urllib.parse import parse_qs, quote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from skystack import jobs
from skystack.remote_access import AccessConfig, identify, may_use_dashboard

logger = logging.getLogger(__name__)

REGISTRY_DIR = jobs.DEFAULT_JOBS_DIR / "web_triage"


def register_session(
    session_id: str,
    target: str,
    package: Path,
    decisions_dir: Path,
    then_argv: List[str],
    next_steps: str,
    requested_by: Optional[str] = None,
    registry_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    registry_dir = Path(registry_dir or REGISTRY_DIR)
    entry = {
        "session_id": session_id, "token": secrets.token_urlsafe(12), "target": target,
        "package": str(package), "decisions_dir": str(decisions_dir), "then_argv": list(then_argv),
        "next_steps": next_steps, "requested_by": requested_by, "status": "open",
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    Path(registry_dir).mkdir(parents=True, exist_ok=True)
    _write(registry_dir, entry)
    return entry


def session_path(entry: Dict[str, Any]) -> str:
    return f"/triage/{entry['session_id']}/{entry['token']}/"


def _write(registry_dir: Path, entry: Dict[str, Any]) -> None:
    p = Path(registry_dir) / f"{entry['session_id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2))
    tmp.replace(p)


def load_session(session_id: str, registry_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    registry_dir = Path(registry_dir or REGISTRY_DIR)
    p = Path(registry_dir) / f"{session_id}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def open_sessions(target: Optional[str] = None, registry_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    registry_dir = Path(registry_dir or REGISTRY_DIR)
    out = []
    for p in sorted(Path(registry_dir).glob("*.json"), reverse=True) if Path(registry_dir).exists() else []:
        try:
            e = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if e.get("status") == "open" and Path(e.get("package", "")).exists() and target in (None, e.get("target")):
            out.append(e)
    return out


def close_session(session_id: str, registry_dir: Optional[Path] = None, **changes) -> None:
    registry_dir = Path(registry_dir or REGISTRY_DIR)
    e = load_session(session_id, registry_dir)
    if e:
        e.update(status="closed", **changes)
        _write(registry_dir, e)


def submit_decisions(entry: Dict[str, Any], decisions: Dict[str, Any], by: Optional[str],
                     registry_dir: Optional[Path] = None, jobs_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Save a session's decisions and queue the follow-up job."""
    registry_dir = Path(registry_dir or REGISTRY_DIR)
    jobs_dir = Path(jobs_dir or jobs.DEFAULT_JOBS_DIR)
    from skystack.triage import DECISIONS_FORMAT

    if entry.get("status") != "open":
        raise ValueError("This review was already finished.")
    if decisions.get("format") != DECISIONS_FORMAT or decisions.get("session_id") != entry["session_id"]:
        raise ValueError("These decisions are not for this review session.")
    who = by or entry.get("requested_by")
    out = Path(entry["decisions_dir"]) / f"{entry['session_id']}_web.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**decisions, "reviewer": decisions.get("reviewer") or who or "web"}, indent=2),
                   encoding="utf-8")
    # the review itself was this person's request, so its follow-up needs no second approval
    job = jobs.submit_request([*entry["then_argv"], str(out)], jobs_dir, requested_by=who)
    close_session(entry["session_id"], registry_dir, submitted_by=who, decisions=str(out), job=job["id"],
                  submitted=datetime.now().isoformat(timespec="seconds"))
    return job


class TriageServer:
    def __init__(self, access: AccessConfig, host: str = "127.0.0.1", port: Optional[int] = None,
                 registry_dir: Optional[Path] = None, jobs_dir: Optional[Path] = None,
                 on_submit=None):
        registry_dir = Path(registry_dir or REGISTRY_DIR)
        jobs_dir = Path(jobs_dir or jobs.DEFAULT_JOBS_DIR)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug("triage server: " + fmt, *args)

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, payload):
                self._send(code, json.dumps(payload).encode())

            def _route(self):
                """(entry, rest, visitor) for /[triage/]<session>/<token>/<rest>, or None after replying."""
                query = parse_qs(urlsplit(self.path).query)
                self.act_as = (query.get("as") or [None])[0]   # testing a login from this PC
                visitor = identify(dict(self.headers), access, act_as=self.act_as)
                ok, why = may_use_dashboard(visitor, access)
                if not ok:
                    self._send(403, why.encode(), "text/plain; charset=utf-8")
                    return None
                parts = [p for p in self.path.split("?")[0].split("/") if p]
                if parts and parts[0] == "triage":   # with or without tailscale's mount path
                    parts = parts[1:]
                entry = load_session(parts[0], registry_dir) if len(parts) >= 2 else None
                if not entry or not secrets.compare_digest(parts[1], entry["token"]):
                    self._send(404, b"No such review.", "text/plain")
                    return None
                return entry, "/".join(parts[2:]), visitor

            def do_GET(self):
                routed = self._route()
                if not routed:
                    return
                entry, rest, _ = routed
                if rest:
                    return self._send(404, b"not found", "text/plain")
                if entry.get("status") != "open":
                    return self._send(410, b"This review was already finished.", "text/plain; charset=utf-8")
                page = Path(entry["package"]).read_bytes()
                if self.act_as:   # keep the test identity for Finish review too
                    page = page.replace(b'"submit_url":"submit"',
                                        b'"submit_url":"submit?as=' + quote(self.act_as).encode() + b'"', 1)
                self._send(200, page, "text/html; charset=utf-8")

            def do_POST(self):
                routed = self._route()
                if not routed:
                    return
                entry, rest, visitor = routed
                if rest != "submit":
                    return self._json(404, {"ok": False, "error": "not found"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                    job = submit_decisions(entry, data, visitor.login, registry_dir, jobs_dir)
                except (ValueError, json.JSONDecodeError) as e:
                    return self._json(400, {"ok": False, "error": str(e)})
                logger.info("Review %s finished by %s; queued %s", entry["session_id"], visitor.label, job["id"])
                if on_submit:
                    on_submit(job)
                self._json(200, {"ok": True, "job": job["id"]})

        self._httpd = ThreadingHTTPServer((host, port if port is not None else access.triage_port), Handler)
        self.port = self._httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "TriageServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
