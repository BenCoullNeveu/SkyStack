"""Streamlit dashboard: every target's processing status, and the pipeline
(run_routine.py) for a selected target, run as background jobs.

Run with:
    streamlit run skystack_ui.py        # just you, on this computer
    python skystack_server.py           # + collaborators over Tailscale, web triage, job queue

Reads/writes the same skystack.db that `python skystack_db.py` refreshes
from the command line.
"""
import json
import socket
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from astro_utils.config_loader import load_config
from skystack_db import SkyStackDatabase

REPO_ROOT = Path(__file__).parent
DB_PATH = REPO_ROOT / "skystack.db"

TARGET_COLUMNS = [
    "name", "total_lights", "nights", "calibrated_frames", "measured_frames", "registered_frames",
    "calibrated_blinked_frames", "true_yield_pct", "stacked_masters", "last_scanned",
]
COLUMN_LABELS = {
    "name": "Target",
    "total_lights": "Lights",
    "nights": "Nights",
    "calibrated_frames": "Calibrated",
    "measured_frames": "Measured",
    "registered_frames": "Registered",
    "calibrated_blinked_frames": "Kept after triage",
    "true_yield_pct": "Yield %",
    "stacked_masters": "Masters",
    "last_scanned": "Last scanned",
}
JOB_STATUS_ICON = {"running": "⏳", "succeeded": "✅", "failed": "❌", "died": "💀", "cancelled": "⛔",
                   "queued": "🕒", "awaiting_approval": "✋", "rejected": "🚫"}

st.set_page_config(page_title="SkyStack", layout="wide")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_scan_dirs(config) -> dict:
    return {
        "light_base_dir": Path(config.get("light_base_dir")),
        "calibrated_base_dir": Path(config.get("calibrated_output_dir")),
        "registered_base_dir": Path(config.get("registered_output_dir") or config.get("masters_dir")),
    }


def load_targets_df() -> pd.DataFrame:
    with SkyStackDatabase(DB_PATH) as db:
        df = pd.read_sql_query("SELECT * FROM targets ORDER BY name ASC", db.conn)
        measured = pd.read_sql_query(
            "SELECT target AS name, COUNT(*) AS measured_frames FROM frame_metrics GROUP BY target", db.conn)
    df = df.merge(measured, on="name", how="left")
    df["measured_frames"] = df["measured_frames"].fillna(0).astype(int)
    for col in TARGET_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df


def rescan():
    with SkyStackDatabase(DB_PATH) as db:
        return db.refresh_all_targets(**scan_dirs)


@st.cache_data(ttl=30, show_spinner=False)
def _session_folders(light_dir: str) -> list:
    d = Path(light_dir)
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


@st.cache_data(ttl=30, show_spinner=False)
def _calibrated_frame_names(config_items: tuple, target: str, sessions: tuple) -> list:
    from skystack import pipeline as P
    paths = P.TargetPaths.from_config(dict(config_items), target)
    try:
        return [f.name for f in P.calibrated_frames(paths, list(sessions) or None)]
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _launch(argv):
    """Start a job now if it may, otherwise queue it as a request.

    The owner's jobs start at once (or queue behind a running one); a
    collaborator's always go through the queue, which starts them inside the
    access window -- after the owner's approval unless they're auto_run."""
    from skystack import jobs

    visitor, access = VISITOR, ACCESS
    try:
        if visitor.role == "owner":
            job = jobs.submit_request(argv, requested_by=visitor.login)
        else:
            job = jobs.submit_request(argv, requested_by=visitor.login,
                                      needs_approval=access.needs_approval(visitor.login))
        started = jobs.start_next_queued(may_start=access.may_start)
    except Exception as e:
        st.error(str(e))
        return
    if not started or started["id"] != job["id"]:
        st.session_state["flash"] = (
            "Waiting for the owner's approval." if job["status"] == "awaiting_approval"
            else "Queued: it starts when the current job finishes." if jobs.running_job()
            else "Queued: it starts when remote access opens.")
    st.rerun()


def _job_line(job) -> str:
    when = f" · started {job['started']}" if job.get("started") else f" · asked {job.get('submitted')}"
    return (f"{JOB_STATUS_ICON.get(job['status'], '')} **{job['stage']}** — {job['status'].replace('_', ' ')}"
            + (f" (exit {job['returncode']})" if job.get("returncode") not in (None, 0) else "")
            + when
            + (f" · ended {job['ended']}" if job.get("ended") else "")
            + (f" · by {job['requested_by']}" if job.get("requested_by") else ""))


def render_job_panel(target_name: str, was_busy: bool):
    """Latest jobs for this target, with a live log while one is running."""
    from skystack import jobs

    # A job was running when the page last fully rendered and now none is:
    # refresh the whole page once so stats, pipeline state and buttons catch up.
    if was_busy and jobs.running_job() is None:
        rescan()
        st.rerun(scope="app")

    target_jobs = jobs.list_jobs(target=target_name)[:5]
    if not target_jobs:
        st.caption("No jobs run for this target from the dashboard yet.")
        return

    latest = target_jobs[0]
    running = latest["status"] == "running"
    c1, c2 = st.columns([5, 1])
    c1.markdown(_job_line(latest))
    if running and _may_manage(latest) and c2.button("Cancel job", key=f"cancel_{latest['id']}"):
        try:
            jobs.cancel_job(latest["id"])
        except RuntimeError as e:
            st.warning(str(e))
        st.rerun(scope="app")
    with st.expander("Log", expanded=running):
        st.code("python run_routine.py " + " ".join(latest["argv"]), language="bash")
        st.code(jobs.read_log(latest, max_lines=60) or "(no output yet)", language="log")
    if len(target_jobs) > 1:
        with st.expander("Earlier jobs"):
            for j in target_jobs[1:]:
                st.markdown(_job_line(j) + f" · log: `{j['log']}`")


def render_transfer_panel(was_busy: bool):
    """Status of the latest Dropbox transfer job, polled while one runs."""
    from skystack import jobs

    if was_busy and jobs.running_job() is None:
        rescan()
        st.rerun(scope="app")

    latest = next((j for j in jobs.list_jobs() if j.get("stage") == "transfer"), None)
    if latest is None:
        return
    st.caption(_job_line(latest).replace("**transfer**", "Last transfer"))
    with st.expander("Transfer log", expanded=latest["status"] == "running"):
        st.code(jobs.read_log(latest, max_lines=60) or "(no output yet)", language="log")


def _save_decision_uploads(uploads, triage_dir: Path, session_id: str | None) -> list:
    """Validate uploaded decisions JSON and save them next to the package."""
    from skystack.triage import DECISIONS_FORMAT

    saved, problems = [], []
    out_dir = triage_dir / "decisions"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for up in uploads:
        try:
            data = json.loads(up.getvalue().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            problems.append(f"{up.name}: not a JSON file")
            continue
        if data.get("format") != DECISIONS_FORMAT:
            problems.append(f"{up.name}: not a SkyStack triage decisions file")
        elif session_id and data.get("session_id") != session_id:
            problems.append(f"{up.name}: belongs to package {data.get('session_id')}, "
                            f"not the latest one ({session_id})")
        else:
            dest = out_dir / f"{stamp}_{Path(up.name).name}"
            dest.write_bytes(up.getvalue())
            saved.append(dest)
    for p in problems:
        st.error(p)
    return [] if problems else saved


# ---------------------------------------------------------------------------
# Target tabs
# ---------------------------------------------------------------------------

def _after_summary(after: dict, presets: dict) -> str:
    """'stacking → BlurXTerminator → client export (auto)' for the options set in Run pipeline."""
    parts = ["stacking"]
    if after["postprocess"]:
        parts.append(" → ".join(presets.get(s, {}).get("label", s) for s in after["steps"]))
    parts.append({None: "client export (auto)", True: "client export", False: "no export"}[after["export"]])
    return " → ".join(parts)


def render_run_tab(ctx) -> dict:
    """Steps 1-10 in order. Returns the after-triage options (8-10), which the
    Triage and Masters & export tabs reuse."""
    from skystack import jobs
    from skystack.postprocess import default_steps

    P, config, target_name, sessions, busy = ctx["P"], ctx["config"], ctx["target"], ctx["sessions"], ctx["busy"]
    presets = ctx["presets"]
    reg_cfg = config.get("registration", {}) or {}
    exp_cfg = config.get("client_export", {}) or {}

    left, right = st.columns(2)
    with left:
        st.markdown("**Before triage**")
        do_transfer = st.checkbox("1. Get new files from Dropbox", value=True, key="pl_transfer")
        do_masters = st.checkbox("2. Stack master flats/darks", value=True, key="pl_masters")
        st.checkbox("3. Calibrate lights", value=True, disabled=True, key="pl_calibrate")
        do_measure = st.checkbox("4. Measure calibrated lights (SubframeSelector)", value=True, key="pl_measure",
                                 help="FWHM, eccentricity, stars, PSF signal weight, ... for the "
                                      "triage tool's subframe selector")
        canvas = st.radio("5. Reference: registration canvas", ["union", "intersection"],
                          index=0 if reg_cfg.get("canvas", "union") == "union" else 1,
                          horizontal=True, key="pl_canvas",
                          help="union keeps every pixel of data; intersection keeps only "
                               "the area every frame covers")
        frames = _calibrated_frame_names(tuple(sorted((k, str(v)) for k, v in config.items()
                                                      if isinstance(v, (str, int, float)))),
                                         target_name, tuple(sessions))
        ref_choice = st.selectbox(
            "Register against", ["Auto: bounding-box canvas"] + frames, key="pl_ref",
            help="Pick a specific calibrated light to register against instead of the "
                 "plate-solved canvas (only frames calibrated already are listed)")
        st.checkbox("6. Register lights", value=True, disabled=True, key="pl_register")
    ref_frame = None if ref_choice.startswith("Auto") else ref_choice

    with right:
        st.markdown("**Triage and after**")
        modes = {"local": "Review here: a triage window on this computer, then carry on",
                 "web": "Review in your browser (link in the Triage tab), then carry on",
                 "send": "Send to reviewers (outbox + Discord) and stop",
                 "build": "Only build the triage page"}
        if not VISITOR.local:   # a window on the server PC is no use to someone elsewhere
            modes.pop("local")
        default_mode = P.default_triage_mode(config) if VISITOR.local else "web"
        triage_mode = st.radio("7. Triage (blink + subframe selector)", list(modes),
                               index=list(modes).index(default_mode) if default_mode in modes else 0,
                               format_func=modes.get, key="pl_triage_mode")
        st.checkbox("8. Stack registered lights", value=True, disabled=True, key="pl_stack")
        post_after = st.checkbox("9. Post-process the masters", key="pl_post_after",
                                 value=P.is_postprocess_after_stack(config))
        post_steps = st.multiselect("Post-processing steps (in order)", list(presets),
                                    default=[s for s in default_steps(config) if s in presets],
                                    format_func=lambda n: presets[n].get("label", n), key="pl_post_steps",
                                    disabled=not post_after)
        is_client = P.is_client_target(config, target_name)
        export_choice = st.radio(
            "10. Client export", ["auto", "yes", "no"], horizontal=True, key="pl_export",
            help=f"auto = only for client_export.client_targets "
                 f"({'this target is one' if is_client else 'this target is not one'})")
        with st.expander("Export options"):
            raw_modes = ["none", "kept", "all"]
            raw_lights = st.selectbox("Raw LIGHT frames", raw_modes, key="pl_raw",
                                      index=raw_modes.index(exp_cfg.get("include_raw_lights", "none")),
                                      help="kept = only frames that passed triage")
            with_cal = st.checkbox("Include master flats/darks", key="pl_cal",
                                   value=bool(exp_cfg.get("include_calibration_masters", False)),
                                   disabled=raw_lights == "none",
                                   help="The masters used to calibrate the included raw frames")
            with_processed = st.checkbox("Include corrected (post-processed) masters", key="pl_with_processed",
                                         value=bool(exp_cfg.get("include_processed", True)))
    after = dict(postprocess=post_after and bool(post_steps), steps=post_steps if post_after else [],
                 export={"auto": None, "yes": True, "no": False}[export_choice],
                 raw_lights=raw_lights, with_calibration=with_cal, processed=with_processed)

    st.caption("Runs 1–7. " + ("After you click **Finish review** in the triage tool, it carries on "
                              "with 8–10." if triage_mode in ("local", "web") else
                              "8–10 run when the reviewers' decisions come back (Triage tab)."))
    if st.button("Run", type="primary", disabled=busy, key="pl_run"):
        _launch(jobs.pipeline_argv(
            "run", target_name, sessions, skip_transfer=not do_transfer, skip_masters=not do_masters,
            skip_measure=not do_measure, triage_mode=triage_mode, canvas=canvas,
            reference_frame=ref_frame, **after))

    with st.expander("Run a single stage"):
        s1, s2 = st.columns([2, 1])
        stage = s1.selectbox("Stage", ["calibrate", "measure", "reference", "register", "stack"], key="pl_stage")
        force = s2.checkbox("Redo frames already done", key="pl_force",
                            disabled=stage not in ("register", "measure"))
        if st.button(f"Run '{stage}'", disabled=busy, key="pl_run_stage"):
            _launch(jobs.pipeline_argv(stage, target_name, sessions, canvas=canvas,
                                       reference_frame=ref_frame, force=force))
    return after


def render_triage_tab(ctx, after: dict):
    from skystack import jobs

    P, paths, state = ctx["P"], ctx["paths"], ctx["state"]
    target_name, sessions, busy = ctx["target"], ctx["sessions"], ctx["busy"]

    # Triage frames that are already registered, without re-running 1-6
    st.markdown("**Open the triage tool on registered frames**")
    candidates = {scope: P.triage_candidates(paths, scope, sessions) for scope in P.TRIAGE_SCOPES}
    scope_labels = {"pending": f"Waiting for triage ({len(candidates['pending'])} frames)",
                    "all": f"All registered frames, including ones already kept/rejected "
                           f"({len(candidates['all'])} frames)"}
    scope = st.radio("Frames", list(scope_labels), format_func=scope_labels.get, key=f"pl_scope_{target_name}",
                     index=0 if candidates["pending"] else 1, horizontal=True,
                     help="'All' redoes a triage: frames you now reject move to REJECTED/, frames you "
                          "keep go to BLINKED/; ones already in the right place aren't touched")
    frames_now = candidates[scope]
    measured = P.measured_frame_count(target_name, frames_now)
    if frames_now and measured < len(frames_now):
        m1, m2 = st.columns([3, 1])
        m1.caption(f"{measured} of {len(frames_now)} frames have metrics for the subframe selector"
                   + (" (it's hidden without them)." if not measured else ".")
                   + " Measuring uses the calibrated frames.")
        if m2.button("Measure first", disabled=busy, key="pl_measure_first"):
            _launch(jobs.pipeline_argv("measure", target_name, sessions))
    carry_on = st.checkbox(f"After the review, carry on: {_after_summary(after, ctx['presets'])}",
                           value=True, key="pl_carry_on",
                           help="Steps 8–10 as set in the Run pipeline tab. Untick to only apply the "
                                "decisions (move kept/rejected frames) and stop.")
    t1, t2, t3 = st.columns(3)
    if t1.button(f"Review in your browser ({len(frames_now)} frames)", type="primary",
                 disabled=busy or not frames_now, key="pl_review_web",
                 help="Builds the triage page; a link to it appears below. Finish review there queues the rest."):
        _launch(jobs.pipeline_argv("triage", target_name, sessions, web=True, triage_scope=scope,
                                   carry_on=carry_on, **after))
    if VISITOR.local and t2.button("Review in a window here", disabled=busy or not frames_now,
                                   key="pl_review_here",
                                   help="Opens the triage tool in its own window on this computer"):
        _launch(jobs.pipeline_argv("triage", target_name, sessions, local=True, triage_scope=scope,
                                   carry_on=carry_on, **after))
    if t3.button("Send to reviewers instead", disabled=busy or not frames_now, key="pl_send",
                 help="Builds the page, copies it to the outbox and posts to Discord"):
        _launch(jobs.pipeline_argv("triage", target_name, sessions, triage_scope=scope))

    render_open_reviews(target_name)

    st.divider()
    st.markdown("**Pages sent to reviewers**")
    triage = state.get("triage")
    session_id = None
    if not triage:
        st.caption("No triage page has been made for this target yet.")
    else:
        session_id = triage.get("session_id")
        package = Path(triage.get("package", ""))
        status = "decisions applied" if triage.get("applied") else "waiting for decisions"
        extra = ""
        if triage.get("applied") and triage.get("metric_rejected") is not None:
            extra = (f" ({triage.get('kept')} kept, {triage.get('rejected')} rejected, "
                     f"{triage.get('metric_rejected')} of them by metric cuts)")
        st.write(f"Latest page `{session_id}` ({triage.get('mode', 'remote')}): "
                 f"{triage.get('n_frames')} frames, {status}{extra}.")
        delivery = triage.get("delivery") or {}
        if delivery:
            st.caption(f"Outbox: {delivery.get('outbox_path') or '—'} · Discord: {delivery.get('discord')}")
        if package.exists() and triage.get("mode") != "local":
            st.download_button("Download triage page", package.read_bytes(), file_name=package.name,
                               mime="text/html", key="pl_dl_pkg")

    uploads = st.file_uploader("Reviewers' decisions files (.json)", type=["json"],
                               accept_multiple_files=True, key=f"pl_uploads_{target_name}",
                               disabled=not triage or triage.get("mode") == "local")
    b1, b2 = st.columns(2)
    if b1.button(f"Apply decisions, then {_after_summary(after, ctx['presets'])}", type="primary",
                 key="pl_finish", disabled=busy or not uploads):
        saved = _save_decision_uploads(uploads, paths.triage_dir, session_id)
        if saved:
            _launch(jobs.pipeline_argv("finish", target_name, sessions, decisions=saved, **after))
    if b2.button("Apply decisions only", key="pl_apply", disabled=busy or not uploads):
        saved = _save_decision_uploads(uploads, paths.triage_dir, session_id)
        if saved:
            _launch(jobs.pipeline_argv("apply-triage", target_name, decisions=saved))


def _triage_server_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", ACCESS.triage_port), timeout=0.3):
            return True
    except OSError:
        return False


def render_open_reviews(target_name: str):
    """Links to this target's web reviews that haven't been finished yet."""
    from skystack.triage_server import close_session, open_sessions, session_path

    sessions = open_sessions(target_name)
    if not sessions:
        return
    st.markdown("**Open reviews**")
    if not _triage_server_up():
        st.warning("The web triage server isn't running, so these links won't open. "
                   "Start SkyStack with `python skystack_server.py`.")
    for e in sessions:
        path = session_path(e)
        url = f"http://127.0.0.1:{ACCESS.triage_port}{path}" if VISITOR.on_this_pc else path
        if VISITOR.on_this_pc and not VISITOR.local:   # testing as someone: the review keeps that identity
            url += f"?as={VISITOR.login}"
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.markdown(f"[Open the triage tool ↗]({url}) · `{e['session_id']}`")
        c2.caption(f"then {e.get('next_steps') or 'apply the decisions'}"
                   + (f" · for {e['requested_by']}" if e.get("requested_by") else ""))
        if c3.button("Discard", key=f"discard_{e['session_id']}", help="Close this review without applying anything"):
            close_session(e["session_id"], discarded_by=VISITOR.login or "owner")
            st.rerun()


def render_results_tab(ctx, after: dict):
    from skystack import jobs
    from skystack.postprocess import default_steps

    P, paths, state, config = ctx["P"], ctx["paths"], ctx["state"], ctx["config"]
    target_name, sessions, busy, presets = ctx["target"], ctx["sessions"], ctx["busy"], ctx["presets"]

    masters = P.stacked_masters(paths)
    processed = P.processed_masters(paths)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Masters** · `{paths.master_dir}`")
        if masters:
            st.dataframe(pd.DataFrame({"File": [m.name for m in masters]}), hide_index=True, width="stretch")
        else:
            st.caption("No masterLight files yet.")
    with c2:
        st.markdown(f"**Corrected (post-processed)** · `{paths.processed_dir}`")
        if processed:
            st.dataframe(pd.DataFrame({"File": [m.name for m in processed]}), hide_index=True, width="stretch")
        else:
            st.caption("None yet. Run post-processing below, or save manually processed files there.")

    st.markdown("**Post-processing**")
    steps_now = after["steps"] or default_steps(config)
    chosen = st.multiselect("Masters to post-process (leave empty for all)", [m.name for m in masters],
                            key=f"pl_post_masters_{target_name}", disabled=not masters)
    st.caption(f"Runs **{' → '.join(presets.get(s, {}).get('label', s) for s in steps_now)}** "
               "(steps from the Run pipeline tab, or the config defaults). The masters themselves are not changed.")
    if st.button("Run post-processing", disabled=busy or not masters, key="pl_post_run"):
        _launch(jobs.pipeline_argv("postprocess", target_name, steps=steps_now, masters=chosen))
    post = state.get("postprocess")
    if post:
        ok = sum(r.get("status") == "ok" for r in post.get("results", []))
        st.caption(f"Last post-processing ({' → '.join(post.get('steps', []))}): {ok} of "
                   f"{len(post.get('results', []))} masters OK · {post.get('completed')}")
        for r in post.get("results", []):
            if r.get("status") != "ok":
                st.warning(f"{Path(r['input']).name}: {r.get('error')}")

    st.markdown("**Client export**")
    export = state.get("export")
    if export:
        st.caption(f"Last export: `{export['zip']}` ({export.get('n_raw', 0)} raw frames, "
                   f"{export.get('n_calibration_masters', 0)} calibration masters, "
                   f"{export.get('n_processed', 0)} processed) · {export.get('completed')}")
    last_zip = Path(export["zip"]) if export and export.get("zip") else None
    e1, e2 = st.columns(2)
    if e1.button("Export zip", disabled=busy or not masters, key="pl_export_only",
                 help="With the export options from the Run pipeline tab"):
        _launch(jobs.pipeline_argv("export", target_name, sessions, raw_lights=after["raw_lights"],
                                   with_calibration=after["with_calibration"], processed=after["processed"]))
    if e2.button(f"Add {len(processed)} corrected masters to the last zip", key="pl_add_processed",
                 disabled=busy or not (processed and last_zip and last_zip.exists()),
                 help="Updates the last client zip in place, without re-exporting; files with the "
                      "same name are replaced"):
        _launch(jobs.pipeline_argv("add-processed", target_name))


def render_target(config: dict, target_name: str, row):
    from skystack import jobs
    from skystack import pipeline as P
    from skystack.postprocess import available_presets

    paths = P.TargetPaths.from_config(config, target_name)
    active = jobs.running_job()
    busy = active is not None
    try:
        presets = available_presets(config)
    except ValueError as e:
        st.error(str(e))
        presets = {}

    def n(col):
        v = row.get(col)
        return int(v) if pd.notna(v) else 0

    cols = st.columns(7)
    cols[0].metric("Lights", n("total_lights"), help=f"{n('nights')} nights")
    cols[1].metric("Calibrated", n("calibrated_frames"))
    cols[2].metric("Measured", n("measured_frames"))
    cols[3].metric("Registered", n("registered_frames"))
    cols[4].metric("Kept after triage", n("calibrated_blinked_frames"))
    y = row.get("true_yield_pct")
    cols[5].metric("Yield", f"{y:.1f}%" if pd.notna(y) else "—", help="Kept after triage ÷ raw lights captured")
    cols[6].metric("Masters", n("stacked_masters"))

    if busy and active.get("target") != target_name:
        owner = f" for **{active['target']}**" if active.get("target") else ""
        st.info(f"A **{active['stage']}** job{owner} is running. New jobs queue and start after it "
                "(one job at a time: all PixInsight work shares one set of control files).")
    st.fragment(render_job_panel, run_every=3 if busy else None)(target_name, busy)

    sessions = st.multiselect(
        "Sessions (leave empty for all)", _session_folders(str(paths.light_dir)), key=f"sessions_{target_name}",
        help="Session folders under LIGHT/<target>/. Applies to calibrate, measure, reference, "
             "register, triage and raw-light export.")

    ctx = {"P": P, "config": config, "target": target_name, "paths": paths, "state": P.load_state(paths),
           "sessions": sessions, "busy": False, "presets": presets}   # busy: jobs queue instead
    tab_run, tab_triage, tab_results, tab_details = st.tabs(
        ["Run pipeline", "Triage", "Masters & export", "Details"])
    with tab_run:
        after = render_run_tab(ctx)
    with tab_triage:
        render_triage_tab(ctx, after)
    with tab_results:
        render_results_tab(ctx, after)
    with tab_details:
        st.caption(f"Folders: `{paths.light_dir}` · `{paths.calibrated_dir}` · `{paths.root}`")
        st.json(ctx["state"], expanded=False)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _may_manage(job) -> bool:
    """The owner manages every job; a collaborator only their own."""
    return VISITOR.role == "owner" or (VISITOR.login and job.get("requested_by") == VISITOR.login)


def render_sidebar():
    from skystack import jobs

    st.sidebar.markdown(f"**Signed in:** {VISITOR.label} · {VISITOR.role}")
    if VISITOR.on_this_pc and not VISITOR.local:
        st.sidebar.warning("Test mode: this tab acts as this login (`?as=` in the address, or "
                           "`skystack_server.py --act-as`). Open the dashboard without it to be the owner.")
    st.sidebar.caption(ACCESS.status_text())
    if VISITOR.role == "owner" and ACCESS.enabled:
        with st.sidebar.expander("Remote access"):
            ov = ACCESS.override()
            labels = {"auto": "Follow the schedule", "open": "Open now", "closed": "Closed now"}
            mode = st.radio("Collaborator access", list(labels), format_func=labels.get,
                            index=list(labels).index(ov["mode"]), key="ra_mode")
            hours = st.number_input("For how many hours (0 = until changed)", 0, 72, 0, key="ra_hours",
                                    disabled=mode == "auto")
            if st.button("Apply", key="ra_apply"):
                from datetime import datetime, timedelta
                ACCESS.set_override(mode, datetime.now() + timedelta(hours=hours) if hours and mode != "auto" else None,
                                    by=VISITOR.login or "owner")
                st.rerun()
            if ACCESS.collaborators:
                st.caption("Collaborators: " + ", ".join(
                    f"{c} ({'runs on its own' if o.get('auto_run') else 'needs approval'})"
                    for c, o in ACCESS.collaborators.items()))
            else:
                st.caption("No collaborators yet (remote_access.collaborators in config.yaml).")

    waiting = jobs.waiting_jobs()
    st.sidebar.markdown(f"**Requests** ({len(waiting)})")
    if not waiting:
        st.sidebar.caption("Nothing waiting.")
    for j in waiting:
        box = st.sidebar.container(border=True)
        box.markdown(_job_line(j))
        box.caption(" ".join(j["argv"]))
        cols = box.columns(3)
        if j["status"] == "awaiting_approval" and VISITOR.role == "owner":
            if cols[0].button("Approve", key=f"ok_{j['id']}"):
                jobs.approve_request(j["id"], by=VISITOR.login or "owner")
                jobs.start_next_queued(may_start=ACCESS.may_start)
                st.rerun()
            if cols[1].button("Reject", key=f"no_{j['id']}"):
                jobs.reject_request(j["id"], by=VISITOR.login or "owner")
                st.rerun()
        if _may_manage(j) and cols[2].button("Cancel", key=f"x_{j['id']}"):
            jobs.cancel_job(j["id"])
            st.rerun()


config = load_config(REPO_ROOT / "config.yaml")
scan_dirs = get_scan_dirs(config)

from skystack import jobs as _jobs  # noqa: E402
from skystack.remote_access import AccessConfig, identify, may_use_dashboard  # noqa: E402

ACCESS = AccessConfig.from_config(config)
VISITOR = identify(st.context.headers, ACCESS, act_as=st.query_params.get("as"))   # ?as=: test a login
_ok, _why = may_use_dashboard(VISITOR, ACCESS)
st.title("SkyStack")
if not _ok:
    st.error(_why)
    st.caption(ACCESS.status_text())
    st.stop()

_jobs.start_next_queued(may_start=ACCESS.may_start)   # move the queue along, even without skystack_server.py
render_sidebar()
if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))
_active_job = _jobs.running_job()
_transfer_busy = _active_job is not None and _active_job.get("stage") == "transfer"
col1, col2, col3 = st.columns([1, 1, 3])
with col1:
    if st.button("Rescan targets", type="primary"):
        with st.spinner("Scanning target directories..."):
            n_targets = rescan()
        st.success(f"Refreshed {n_targets} targets.")
        st.rerun()
with col2:
    if st.button("Transfer from Dropbox",
                 help=f"Move new files from {config.get('dropbox_source')} to "
                      f"{config.get('local_target')} (runs in the background; "
                      "targets are rescanned when it finishes)"):
        _launch(["transfer"])
with col3:
    st.fragment(render_transfer_panel, run_every=3 if _transfer_busy else None)(_transfer_busy)

df = load_targets_df()
if df.empty:
    st.info("No targets yet. Click **Rescan targets** to scan your LIGHT directory.")
    st.stop()

with st.expander(f"All targets ({len(df)})", expanded=False):
    st.dataframe(df[TARGET_COLUMNS].rename(columns=COLUMN_LABELS), width="stretch", hide_index=True)

names = sorted(df["name"])
target_name = st.selectbox("Target", names, key="target")
st.subheader(target_name)
render_target(config, target_name, df.loc[df["name"] == target_name].iloc[0].to_dict())
