"""Remote access to the dashboard over Tailscale.

The dashboard (Streamlit) and the web triage server only listen on
127.0.0.1. `tailscale serve` publishes them on this machine's tailnet HTTPS
address and tells them who each visitor is (Tailscale-User-Login header),
so there are no passwords:

    owner         local visitors (http://localhost), and the logins in
                  remote_access.owners -- always allowed, jobs start at once
    collaborator  logins in remote_access.collaborators -- the full dashboard,
                  but only inside the access windows (default 22:00-07:00
                  local time), and their jobs start only inside a window;
                  per collaborator, jobs either run on their own
                  (auto_run: true) or wait for the owner's approval
    anyone else   refused

The owner can override the schedule (open now / closed now) from the
dashboard; that's kept in pipeline_jobs/remote_access.json.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / "pipeline_jobs" / "remote_access.json"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_WINDOWS = [{"days": "all", "start": "22:00", "end": "07:00"}]
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# Testing: treat visits from this PC as this Tailscale login (e.g. a collaborator's),
# so their view, the access window and approvals can be tried without a second device.
ACT_AS_ENV = "SKYSTACK_ACT_AS"
OVERRIDES = ("auto", "open", "closed")


@dataclass(frozen=True)
class Window:
    days: Tuple[int, ...]   # weekday the window *starts* on (0 = Monday)
    start: dtime
    end: dtime              # end <= start means it runs past midnight

    def contains(self, now: datetime) -> bool:
        t = now.time()
        if self.end > self.start:
            return now.weekday() in self.days and self.start <= t < self.end
        # overnight: the evening part on a listed day, or the morning after one
        return ((now.weekday() in self.days and t >= self.start)
                or ((now.weekday() - 1) % 7 in self.days and t < self.end))


def _parse_time(v: Any) -> dtime:
    return datetime.strptime(str(v).strip(), "%H:%M").time()


def _parse_days(v: Any) -> Tuple[int, ...]:
    if v in (None, "all", "*"):
        return tuple(range(7))
    names = [v] if isinstance(v, str) else list(v)
    try:
        return tuple(sorted({DAYS.index(str(n).strip().lower()[:3]) for n in names}))
    except ValueError as e:
        raise ValueError(f"remote_access.windows: unknown day in {v!r} (use mon..sun or 'all')") from e


@dataclass
class AccessConfig:
    enabled: bool = False
    owners: List[str] = field(default_factory=list)
    collaborators: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    windows: List[Window] = field(default_factory=list)
    dashboard_port: int = 8501
    triage_port: int = 8502
    manage_tailscale_serve: bool = False
    state_file: Path = STATE_FILE

    @classmethod
    def from_config(cls, config: Mapping[str, Any], state_file: Path = STATE_FILE) -> "AccessConfig":
        cfg = dict(config.get("remote_access", {}) or {})
        collaborators = {}
        for login, opts in (cfg.get("collaborators") or {}).items():
            collaborators[str(login).lower()] = dict(opts or {})
        windows = [Window(_parse_days(w.get("days", "all")), _parse_time(w["start"]), _parse_time(w["end"]))
                   for w in (cfg.get("windows") or DEFAULT_WINDOWS)]
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            owners=[str(o).lower() for o in (cfg.get("owners") or [])],
            collaborators=collaborators,
            windows=windows,
            dashboard_port=int(cfg.get("dashboard_port", 8501)),
            triage_port=int(cfg.get("triage_port", 8502)),
            manage_tailscale_serve=bool(cfg.get("manage_tailscale_serve", False)),
            state_file=state_file,
        )

    # ---- schedule ----------------------------------------------------------

    def override(self) -> Dict[str, Any]:
        """{"mode": auto|open|closed, "until": iso or None, "by": login}; expired overrides read as auto."""
        try:
            data = json.loads(Path(self.state_file).read_text())
        except (OSError, json.JSONDecodeError):
            return {"mode": "auto", "until": None}
        until = data.get("until")
        if data.get("mode") not in OVERRIDES or (until and datetime.fromisoformat(until) <= datetime.now()):
            return {"mode": "auto", "until": None}
        return data

    def set_override(self, mode: str, until: Optional[datetime] = None, by: Optional[str] = None) -> None:
        if mode not in OVERRIDES:
            raise ValueError(f"override must be one of {OVERRIDES}")
        Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(self.state_file).write_text(json.dumps(
            {"mode": mode, "until": until.isoformat(timespec="minutes") if until else None, "by": by}))

    def scheduled_open(self, now: datetime) -> bool:
        return any(w.contains(now) for w in self.windows)

    def is_open(self, now: Optional[datetime] = None) -> bool:
        """Whether collaborators may use the dashboard (and their jobs may start) now."""
        if not self.enabled:
            return False
        mode = self.override()["mode"]
        if mode != "auto":
            return mode == "open"
        return self.scheduled_open(now or datetime.now())

    def next_change(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """When the schedule next opens (if closed now) or closes (if open), to the minute."""
        now = (now or datetime.now()).replace(second=0, microsecond=0)
        state = self.scheduled_open(now)
        boundaries = set()
        for d in range(0, 9):
            day = (now + timedelta(days=d)).date()
            for w in self.windows:
                boundaries.add(datetime.combine(day, w.start))
                boundaries.add(datetime.combine(day, w.end))
        for b in sorted(t for t in boundaries if t > now):
            if self.scheduled_open(b) != state:
                return b
        return None

    def status_text(self, now: Optional[datetime] = None) -> str:
        now = now or datetime.now()
        if not self.enabled:
            return "Remote access is off (remote_access.enabled)."
        ov = self.override()
        open_now = self.is_open(now)
        if ov["mode"] != "auto":
            until = f" until {datetime.fromisoformat(ov['until']):%a %H:%M}" if ov.get("until") else ""
            return f"Remote access {'open' if open_now else 'closed'} (set by hand{until})."
        nxt = self.next_change(now)
        when = f" — {'closes' if open_now else 'opens'} {nxt:%a %H:%M}" if nxt else ""
        return f"Remote access {'open' if open_now else 'closed'}{when}."

    # ---- who's visiting ------------------------------------------------------

    def role(self, login: Optional[str]) -> str:
        login = (login or "").lower()
        if login and login in self.owners:
            return "owner"
        if login and login in self.collaborators:
            return "collaborator"
        return "denied"

    def needs_approval(self, login: Optional[str]) -> bool:
        return not bool(self.collaborators.get((login or "").lower(), {}).get("auto_run", False))

    def may_start(self, job: Mapping[str, Any], now: Optional[datetime] = None) -> bool:
        """Queue policy: the owner's requests start any time, collaborators' only inside a window."""
        who = job.get("requested_by")
        return not who or self.role(who) == "owner" or self.is_open(now)


@dataclass(frozen=True)
class Visitor:
    login: Optional[str]
    name: Optional[str]
    role: str            # owner | collaborator | denied
    local: bool          # the owner at this machine (not through tailscale serve)
    on_this_pc: bool = False   # connected to 127.0.0.1 directly (also when testing with ACT_AS_ENV)

    @property
    def label(self) -> str:
        return "this computer" if self.local else (self.name or self.login or "unknown")


def identify(headers: Mapping[str, str], access: AccessConfig, act_as: Optional[str] = None) -> Visitor:
    """Who sent this request. Direct local access (no proxy in between) is the
    owner; anything through `tailscale serve` must carry an allowed Tailscale
    identity. Header names are matched case-insensitively.

    act_as (e.g. the dashboard's ?as=<login>) or ACT_AS_ENV makes a direct
    local visit count as that login instead, for testing a collaborator's
    view next to the owner's. It's ignored for anything coming through the proxy."""
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    host = h.get("host") or ""
    host = host[1:].split("]")[0] if host.startswith("[") else host.split(":")[0]   # drop the port
    login = h.get("tailscale-user-login")
    proxied = bool(login) or "x-forwarded-for" in h or "x-forwarded-host" in h
    if not proxied and host in LOCAL_HOSTS:
        act_as = (act_as or os.environ.get(ACT_AS_ENV, "")).strip()
        if act_as:   # testing: see this PC as that login (can only lower the owner's rights)
            return Visitor(act_as.lower(), f"{act_as} (test)", access.role(act_as), False, on_this_pc=True)
        return Visitor(None, None, "owner", True, on_this_pc=True)
    return Visitor(login, h.get("tailscale-user-name"), access.role(login), False)


def may_use_dashboard(visitor: Visitor, access: AccessConfig, now: Optional[datetime] = None) -> Tuple[bool, str]:
    if visitor.role == "owner":
        return True, ""
    if visitor.role == "collaborator":
        if not access.enabled:
            return False, "Remote access is turned off."
        if access.is_open(now):
            return True, ""
        nxt = access.next_change(now) if access.override()["mode"] == "auto" else None
        return False, "The dashboard is closed right now" + (f"; it opens {nxt:%A %H:%M}." if nxt else ".")
    who = visitor.login or "your device"
    return False, (f"{who} isn't on the list of people who can use this dashboard. "
                   "Ask the owner to add you under remote_access.collaborators.")


# ---- tailscale serve -------------------------------------------------------------

def tailscale_exe() -> Optional[str]:
    return shutil.which("tailscale") or next(
        (p for p in (r"C:\Program Files\Tailscale\tailscale.exe",) if Path(p).exists()), None)


def tailscale_serve(on: bool, access: AccessConfig) -> bool:
    """Publish (or withdraw) the dashboard and triage server on the tailnet."""
    exe = tailscale_exe()
    if not exe:
        logger.warning("tailscale not found; install it to reach the dashboard remotely")
        return False
    cmds = ([[exe, "serve", "--bg", f"http://127.0.0.1:{access.dashboard_port}"],
             [exe, "serve", "--bg", "--set-path", "/triage", f"http://127.0.0.1:{access.triage_port}/triage"]]
            if on else [[exe, "serve", "reset"]])
    ok = True
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            logger.error("%s failed: %s", " ".join(cmd[1:]), (r.stderr or r.stdout).strip())
            ok = False
    return ok
