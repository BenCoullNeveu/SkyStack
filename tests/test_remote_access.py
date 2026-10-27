"""Remote access: access windows and overrides, who a visitor is (Tailscale
identity vs. local), the job request queue, and web triage in a reviewer's
own browser (build -> serve -> Finish review -> follow-up job queued)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from skystack import jobs
from skystack import pipeline as P
from skystack.remote_access import AccessConfig, identify, may_use_dashboard
from skystack.triage_server import TriageServer, load_session, open_sessions, session_path
from tests.test_frame_metrics import _measured_and_registered
from tests.test_pipeline import TARGET, tree  # noqa: F401  (fixture)

MON = datetime(2026, 10, 26)   # a Monday


def _access(tmp_path, **cfg):
    base = {"enabled": True, "owners": ["me@example.com"],
            "collaborators": {"pal@example.com": {"auto_run": True}, "new@example.com": {}}}
    return AccessConfig.from_config({"remote_access": {**base, **cfg}}, state_file=tmp_path / "ra.json")


# ---- schedule -------------------------------------------------------------------

def test_default_window_runs_overnight(tmp_path):
    a = _access(tmp_path)
    assert not a.is_open(MON.replace(hour=21, minute=59))
    assert a.is_open(MON.replace(hour=22))
    assert a.is_open(MON.replace(hour=6, minute=59))      # Sunday night's window
    assert not a.is_open(MON.replace(hour=7))
    assert a.next_change(MON.replace(hour=12)) == MON.replace(hour=22)
    assert a.next_change(MON.replace(hour=23)) == (MON + timedelta(days=1)).replace(hour=7)


def test_windows_on_given_days_and_disabled_access(tmp_path):
    a = _access(tmp_path, windows=[{"days": ["fri"], "start": "22:00", "end": "07:00"},
                                   {"days": "sat", "start": "09:00", "end": "12:00"}])
    fri = MON + timedelta(days=4)
    assert a.is_open(fri.replace(hour=23)) and a.is_open((fri + timedelta(days=1)).replace(hour=3))
    assert not a.is_open((fri - timedelta(days=1)).replace(hour=23))     # Thursday night
    assert a.is_open((fri + timedelta(days=1)).replace(hour=10))         # Saturday morning
    assert not _access(tmp_path, enabled=False).is_open(fri.replace(hour=23))
    with pytest.raises(ValueError, match="unknown day"):
        _access(tmp_path, windows=[{"days": ["someday"], "start": "1:00", "end": "2:00"}])


def test_override_wins_until_it_expires(tmp_path):
    a = _access(tmp_path)
    noon = datetime.now().replace(hour=12, minute=0)
    a.set_override("open", until=datetime.now() + timedelta(hours=1))
    assert a.is_open(noon) and "set by hand" in a.status_text(noon)
    a.set_override("open", until=datetime.now() - timedelta(minutes=1))   # expired -> schedule again
    assert a.override()["mode"] == "auto"
    a.set_override("closed")
    assert not a.is_open(MON.replace(hour=23))


# ---- identity ---------------------------------------------------------------------

def test_identify_local_owner_tailscale_users_and_strangers(tmp_path):
    a = _access(tmp_path)
    assert identify({"Host": "localhost:8501"}, a).role == "owner"
    assert identify({"Host": "127.0.0.1:8502"}, a).local
    ts = {"Host": "pc.tail1234.ts.net", "X-Forwarded-For": "100.64.0.9"}
    assert identify({**ts, "Tailscale-User-Login": "Me@Example.com"}, a).role == "owner"
    pal = identify({**ts, "Tailscale-User-Login": "pal@example.com", "Tailscale-User-Name": "Pal"}, a)
    assert (pal.role, pal.label, pal.local) == ("collaborator", "Pal", False)
    # through the proxy without an identity (e.g. a tagged device), even if Host looks local
    assert identify({"Host": "127.0.0.1:8501", "X-Forwarded-For": "100.64.0.9"}, a).role == "denied"
    assert identify({**ts, "Tailscale-User-Login": "eve@example.com"}, a).role == "denied"


def test_dashboard_access_by_role_and_window(tmp_path):
    a = _access(tmp_path)
    ts = {"Host": "pc.ts.net", "X-Forwarded-For": "100.64.0.9"}
    pal = identify({**ts, "Tailscale-User-Login": "pal@example.com"}, a)
    me = identify({**ts, "Tailscale-User-Login": "me@example.com"}, a)
    day, night = MON.replace(hour=13), MON.replace(hour=23)
    assert may_use_dashboard(pal, a, night)[0] and not may_use_dashboard(pal, a, day)[0]
    assert "opens Monday 22:00" in may_use_dashboard(pal, a, day)[1]
    assert may_use_dashboard(me, a, day)[0]
    eve = identify({**ts, "Tailscale-User-Login": "eve@example.com"}, a)
    assert not may_use_dashboard(eve, a, night)[0]


# ---- queue ----------------------------------------------------------------------------

@pytest.fixture
def no_spawn(monkeypatch):
    """Replace subprocess for the jobs module only (patching subprocess.Popen itself would break everything)."""
    import subprocess
    import types

    spawned = []
    fake = types.SimpleNamespace(**vars(subprocess))
    fake.Popen = lambda cmd, **kw: spawned.append(cmd)
    monkeypatch.setattr(jobs, "subprocess", fake)
    return spawned


def test_queue_runs_one_at_a_time_and_respects_approval_and_window(tmp_path, no_spawn):
    d = tmp_path / "jobs"
    a = _access(tmp_path)
    pal = jobs.submit_request(["stack", "-t", "A"], d, requested_by="pal@example.com",
                              needs_approval=a.needs_approval("pal@example.com"))
    new = jobs.submit_request(["stack", "-t", "B"], d, requested_by="new@example.com",
                              needs_approval=a.needs_approval("new@example.com"))
    assert (pal["status"], new["status"]) == ("queued", "awaiting_approval")

    a.set_override("closed")   # outside the window: collaborators' jobs wait
    assert jobs.start_next_queued(d, may_start=a.may_start) is None
    owner = jobs.submit_request(["stack", "-t", "C"], d)   # the owner's start any time
    assert jobs.start_next_queued(d, may_start=a.may_start)["id"] == owner["id"]
    assert jobs.start_next_queued(d, may_start=a.may_start) is None   # one at a time
    jobs._write_job(d, {**jobs.read_job(owner["id"], d), "status": "succeeded"})

    a.set_override("open")
    jobs.approve_request(new["id"], d, by="me")
    assert jobs.start_next_queued(d, may_start=a.may_start)["id"] == pal["id"]   # oldest first
    jobs._write_job(d, {**jobs.read_job(pal["id"], d), "status": "succeeded"})
    jobs.cancel_job(new["id"], d)
    assert jobs.read_job(new["id"], d)["status"] == "cancelled" and jobs.waiting_jobs(d) == []
    assert len(no_spawn) == 2
    with pytest.raises(RuntimeError, match="cancelled"):
        jobs.approve_request(new["id"], d)


# ---- web triage ------------------------------------------------------------------------

def _req(url, data=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 method="POST" if data is not None else "GET", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_web_triage_serves_the_page_and_queues_the_follow_up(tree, tmp_path):
    config, paths, *_ = tree
    _measured_and_registered(config, paths)
    then = ["finish", "-t", TARGET, "--no-export"]
    entry = P.stage_triage_web(config, TARGET, then_argv=then, next_steps="stacking",
                               requested_by="pal@example.com")
    assert open_sessions(TARGET)[0]["session_id"] == entry["session_id"]
    assert P.load_state(paths)["triage"]["mode"] == "web"

    a = _access(tmp_path)
    server = TriageServer(a, port=0, jobs_dir=tmp_path / "jobs").start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        code, page = _req(base + session_path(entry))
        assert code == 200 and b'"submit_url":"submit"' in page
        assert _req(base + f"/{entry['session_id']}/{entry['token']}/")[0] == 200   # mount path stripped
        assert _req(base + f"/triage/{entry['session_id']}/wrong/")[0] == 404
        stranger = {"X-Forwarded-For": "100.64.0.9", "Tailscale-User-Login": "eve@example.com"}
        assert _req(base + session_path(entry), headers=stranger)[0] == 403

        a.set_override("open")
        pal = {"X-Forwarded-For": "100.64.0.9", "Tailscale-User-Login": "pal@example.com", "Content-Type": "application/json"}
        assert _req(base + session_path(entry) + "submit", {"format": "nope"}, pal)[0] == 400
        code, body = _req(base + session_path(entry) + "submit",
                          {"format": "SkyStack triage decisions", "session_id": entry["session_id"],
                           "discarded": [], "total": 8, "kept_count": 8}, pal)
        assert code == 200 and json.loads(body)["ok"]
        assert _req(base + session_path(entry))[0] == 410          # finished: page no longer served
    finally:
        server.stop()

    (job,) = jobs.waiting_jobs(tmp_path / "jobs")
    decisions = paths.triage_dir / "decisions" / f"{entry['session_id']}_web.json"
    assert job["argv"] == [*then, str(decisions)] and job["requested_by"] == "pal@example.com"
    assert job["status"] == "queued"            # the review was approved work: no second approval
    assert json.loads(decisions.read_text())["reviewer"] == "pal@example.com"
    assert load_session(entry["session_id"])["status"] == "closed" and open_sessions(TARGET) == []
    assert P.stage_apply_triage(config, TARGET, [decisions])["kept"] == 8


def test_web_and_queue_argv():
    assert jobs.pipeline_argv("triage", "M 31", web=True, carry_on=False) == [
        "triage", "-t", "M 31", "--web", "--no-continue"]
    assert jobs.pipeline_argv("run", "M 31", triage_mode="web")[-2:] == ["--triage", "web"]


# ---- testing a collaborator's view from this PC ----------------------------------------

def test_act_as_only_lowers_a_local_visit(tmp_path, monkeypatch):
    a = _access(tmp_path)
    pal = identify({"Host": "localhost:8501"}, a, act_as="Pal@example.com")
    assert (pal.role, pal.login, pal.local, pal.on_this_pc) == ("collaborator", "pal@example.com", False, True)
    assert identify({"Host": "localhost:8501"}, a, act_as="eve@example.com").role == "denied"
    # never honoured through the proxy: a remote visitor can't pick an identity
    remote = {"Host": "pc.ts.net", "X-Forwarded-For": "100.64.0.9", "Tailscale-User-Login": "eve@example.com"}
    assert identify(remote, a, act_as="me@example.com").role == "denied"
    monkeypatch.setenv("SKYSTACK_ACT_AS", "pal@example.com")
    assert identify({"Host": "127.0.0.1:8501"}, a).role == "collaborator"


def test_web_review_opened_as_a_test_login_keeps_that_identity(tree, tmp_path):
    config, paths, *_ = tree
    _measured_and_registered(config, paths)
    entry = P.stage_triage_web(config, TARGET, then_argv=["apply-triage", "-t", TARGET])
    a = _access(tmp_path)
    server = TriageServer(a, port=0, jobs_dir=tmp_path / "jobs").start()
    url = f"http://127.0.0.1:{server.port}{session_path(entry)}"
    try:
        a.set_override("closed")
        assert _req(url + "?as=pal@example.com")[0] == 403        # collaborator outside the window
        assert _req(url)[0] == 200                                 # the owner, any time
        a.set_override("open")
        code, page = _req(url + "?as=pal@example.com")
        assert code == 200 and b'"submit_url":"submit?as=pal%40example.com"' in page
        code, _ = _req(url + "submit?as=pal@example.com",
                       {"format": "SkyStack triage decisions", "session_id": entry["session_id"], "discarded": []},
                       {"Content-Type": "application/json"})
        assert code == 200
    finally:
        server.stop()
    (job,) = jobs.waiting_jobs(tmp_path / "jobs")
    assert job["requested_by"] == "pal@example.com"
