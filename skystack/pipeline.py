"""End-to-end processing pipeline, one stage per function.

    [transfer] -> [masters] -> calibrate -> measure -> reference -> register
        -> triage (blink + subframe selector on the measured metrics), either
           here (local review window, then carries on) or sent to reviewers
           (... wait for their decisions ...)
    apply-triage -> stack -> [postprocess] -> [export]

Directory layout per target (REGISTERED/<target>/):

    reference/   plate-solve canvas reference image (+ .json geometry)
    registered/  registered lights waiting for triage
    BLINKED/     registered lights kept after triage  -> stacked
    REJECTED/    registered lights rejected in triage, by eye or by metric cuts (unless deleted)
    triage/      HTML packages + their local manifests; decisions/ holds returned decisions
    ln/          local normalization data and LN references
    master/      masterLight_*.xisf
    processed/   post-processed copies of the masters (BlurXTerminator, ...)
    pipeline_state.json   what each stage last did

Every stage reads what it needs from disk (not from an earlier stage's
return value) so any stage can be re-run on its own.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from astro_utils.fits_helpers import read_header_info

logger = logging.getLogger(__name__)

SKYSTACK_DIR = Path(__file__).resolve().parent.parent
IMAGE_SUFFIXES = {".fits", ".fit", ".fts", ".xisf"}
REGISTERED_POSTFIX = "_r"


# ---------------------------------------------------------------------------
# Paths and state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetPaths:
    target: str
    light_dir: Path
    calibrated_dir: Path
    root: Path

    @classmethod
    def from_config(cls, config: Mapping[str, Any], target: str) -> "TargetPaths":
        registered = config.get("registered_output_dir") or config.get("masters_dir")
        return cls(
            target=target,
            light_dir=Path(config["light_base_dir"]) / target,
            calibrated_dir=Path(config["calibrated_output_dir"]) / target,
            root=Path(registered) / target,
        )

    reference_dir = property(lambda self: self.root / "reference")
    registered_dir = property(lambda self: self.root / "registered")
    blinked_dir = property(lambda self: self.root / "BLINKED")
    rejected_dir = property(lambda self: self.root / "REJECTED")
    triage_dir = property(lambda self: self.root / "triage")
    ln_dir = property(lambda self: self.root / "ln")
    master_dir = property(lambda self: self.root / "master")
    processed_dir = property(lambda self: self.root / "processed")
    state_file = property(lambda self: self.root / "pipeline_state.json")


def load_state(paths: TargetPaths) -> Dict[str, Any]:
    if paths.state_file.exists():
        return json.loads(paths.state_file.read_text())
    return {"target": paths.target}


def save_state(paths: TargetPaths, state: Dict[str, Any]) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(json.dumps(state, indent=2, default=str))


def _record(paths: TargetPaths, stage: str, info: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state(paths)
    state[stage] = {**info, "completed": datetime.now().isoformat(timespec="seconds")}
    save_state(paths, state)
    return state


# ---------------------------------------------------------------------------
# Frame discovery
# ---------------------------------------------------------------------------

def _images_under(root: Path, exclude_dirs=("rejected",)) -> List[Path]:
    if not root.exists():
        return []
    exclude = {d.lower() for d in exclude_dirs}
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        and not any(part.lower() in exclude for part in p.relative_to(root).parts[:-1])
    )


def raw_stem(path: Path) -> str:
    """Stem of the raw LIGHT a calibrated ("__DDMMYYYY_c") or registered
    ("__DDMMYYYY_c_r") file came from."""
    stem = Path(path).stem
    return stem.rsplit("__", 1)[0] if "__" in stem else stem


def session_raw_stems(paths: TargetPaths, sessions: Optional[Iterable[str]]) -> Optional[set]:
    """Raw-frame stems for the selected session folders (None = all sessions)."""
    if not sessions:
        return None
    stems = set()
    for s in sessions:
        session_dir = paths.light_dir / s
        if not session_dir.exists():
            raise FileNotFoundError(f"Session folder not found: {session_dir}")
        stems |= {p.stem for p in _images_under(session_dir, exclude_dirs=())}
    return stems


def _filter_sessions(frames: List[Path], stems: Optional[set]) -> List[Path]:
    return frames if stems is None else [f for f in frames if raw_stem(f) in stems]


def calibrated_frames(paths: TargetPaths, sessions: Optional[Iterable[str]] = None) -> List[Path]:
    frames = [p for p in _images_under(paths.calibrated_dir) if p.stem.endswith("_c")]
    # one entry per raw frame even if an old run left copies in subfolders
    unique = {}
    for f in frames:
        unique.setdefault(f.stem, f)
    return _filter_sessions(sorted(unique.values()), session_raw_stems(paths, sessions))


# Folders under registered/ that never hold frames waiting for triage: earlier
# kept/rejected frames, and WBPP's local-normalization reference data.
_NOT_PENDING_DIRS = ("rejected", "blinked", "ln_reference_frame_data")
TRIAGE_SCOPES = ("pending", "all")


def pending_registered_frames(paths: TargetPaths, sessions: Optional[Iterable[str]] = None) -> List[Path]:
    """Registered frames not triaged yet: loose in registered/ (this pipeline)
    or in WBPP's per-group folders registered/<group>/."""
    frames = [p for p in _images_under(paths.registered_dir, exclude_dirs=_NOT_PENDING_DIRS)
              if p.stem.endswith(REGISTERED_POSTFIX)]
    return _filter_sessions(frames, session_raw_stems(paths, sessions))


def triage_candidates(
    paths: TargetPaths,
    scope: str = "pending",
    sessions: Optional[Iterable[str]] = None,
) -> List[Path]:
    """Frames for a triage page.

    pending: not triaged yet (pending_registered_frames).
    all:     every registered frame, including ones already kept (BLINKED/,
             or a WBPP group's BLINKED/) or rejected (REJECTED/), to redo a
             triage. One file per raw frame; a frame found in several places
             is taken from BLINKED/ first, since that's what gets stacked.
    """
    if scope not in TRIAGE_SCOPES:
        raise ValueError(f"Unknown triage scope {scope!r}; use one of {TRIAGE_SCOPES}")
    if scope == "pending":
        return pending_registered_frames(paths, sessions)

    def registered(ps):
        return [p for p in ps if p.stem.endswith(REGISTERED_POSTFIX)]

    sources = [  # in order of preference
        registered(_images_under(paths.blinked_dir, exclude_dirs=())),
        registered(p for p in _images_under(paths.registered_dir, exclude_dirs=("rejected", "ln_reference_frame_data"))
                   if "blinked" in (part.lower() for part in p.relative_to(paths.registered_dir).parts[:-1])),
        pending_registered_frames(paths),
        registered(_images_under(paths.rejected_dir, exclude_dirs=())),
    ]
    chosen: Dict[str, Path] = {}
    for group in sources:
        for p in group:
            chosen.setdefault(raw_stem(p), p)
    return _filter_sessions(sorted(chosen.values()), session_raw_stems(paths, sessions))


def already_registered_stems(paths: TargetPaths) -> set:
    stems = set()
    for d in (paths.registered_dir, paths.blinked_dir, paths.rejected_dir):
        stems |= {raw_stem(p) for p in _images_under(d, exclude_dirs=())}
    return stems


# ---------------------------------------------------------------------------
# Stages 1-3: transfer, master calibration frames, calibration
# ---------------------------------------------------------------------------

def _run_script(name: str) -> None:
    logger.info("Running %s ...", name)
    subprocess.run([sys.executable, str(SKYSTACK_DIR / name)], check=True, cwd=SKYSTACK_DIR)


def stage_transfer(config: Mapping[str, Any]) -> None:
    _run_script("auto_transfer.py")


def stage_masters(config: Mapping[str, Any]) -> None:
    _run_script("stack_flats.py")
    _run_script("stack_darks.py")
    if str(config.get("master_index", "db")).lower() == "parquet":
        if config.get("create_index_files"):
            _run_script("create_master_index_parquets.py")
        return
    from skystack_db import SkyStackDatabase

    with SkyStackDatabase(SKYSTACK_DIR / "skystack.db") as db:
        db.sync_master_calibrations(Path(config["flat_master_output"]), "FLAT")
        db.sync_master_calibrations(Path(config["dark_master_output"]), "DARK")


def stage_calibrate(config: Mapping[str, Any], target: str, sessions: Optional[List[str]] = None) -> Path:
    import calibrate_images

    out = calibrate_images.run_calibration_for_target(target, sessions=sessions)
    _record(TargetPaths.from_config(config, target), "calibrate", {"sessions": sessions})
    return out


# ---------------------------------------------------------------------------
# Stage 4: reference (bounding box of all frames, or a chosen light)
# ---------------------------------------------------------------------------

def _resolve_reference_frame(choice: str, frames: List[Path]) -> Path:
    p = Path(choice)
    if p.is_absolute():
        if not p.exists():
            raise FileNotFoundError(f"Reference frame not found: {p}")
        return p
    matches = [f for f in frames if f.name == choice or f.stem == p.stem or raw_stem(f) == p.stem]
    if not matches:
        raise FileNotFoundError(f"No calibrated frame of this target matches {choice!r}")
    return matches[0]


def stage_reference(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]] = None,
    reference_frame: Optional[str] = None,
    canvas_mode: Optional[str] = None,
) -> Path:
    """Choose what to register against.

    Default: plate-solve every calibrated frame, build a canvas covering
    their union (or intersection) and render a reference image on it.
    With reference_frame (a path or file name): use that light as-is.
    """
    from skystack.canvas import build_canvas, choose_orientation_frame, render_reference
    from skystack.platesolve import solve_frames

    paths = TargetPaths.from_config(config, target)
    reg_cfg = dict(config.get("registration", {}) or {})
    reference_frame = reference_frame or reg_cfg.get("reference_frame")
    canvas_mode = canvas_mode or reg_cfg.get("canvas", "union")

    frames = calibrated_frames(paths, sessions)
    if not frames:
        raise RuntimeError(f"No calibrated frames found for {target} in {paths.calibrated_dir}")

    if reference_frame:
        ref = _resolve_reference_frame(reference_frame, frames)
        logger.info("Registering against user-selected frame %s", ref.name)
        _record(paths, "reference", {"mode": "frame", "reference": str(ref), "sessions": sessions})
        return ref

    solved_map = solve_frames(frames, config)
    solved = [s for s in solved_map.values() if s is not None]
    unsolved = [str(p) for p, s in solved_map.items() if s is None]
    if not solved:
        ref = frames[len(frames) // 2]
        logger.warning("No frame could be plate solved; falling back to registering against %s", ref.name)
        _record(paths, "reference", {"mode": "frame-fallback", "reference": str(ref),
                                     "unsolved": unsolved, "sessions": sessions})
        return ref

    filters = {s.path: read_header_info(s.path).get("FILTER") for s in solved}
    orientation = choose_orientation_frame(solved, filters, reg_cfg.get("preferred_filters", ["L"]))
    canvas = build_canvas(solved, orientation, mode=canvas_mode)

    label = "_".join(sessions) if sessions else "all"
    ref_path = paths.reference_dir / f"reference_{canvas_mode}_{label}.xisf"
    render_reference(orientation, canvas, ref_path,
                     extra_keywords={"FILTER": filters.get(orientation.path), "OBJECT": target})
    _record(paths, "reference", {
        "mode": canvas_mode, "reference": str(ref_path), "source_frame": str(orientation.path),
        "canvas": canvas.to_dict(), "unsolved": unsolved, "sessions": sessions,
    })
    return ref_path


# ---------------------------------------------------------------------------
# Stage 5: registration
# ---------------------------------------------------------------------------

def stage_register(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]] = None,
    reference: Optional[Path] = None,
    force: bool = False,
    backend=None,
) -> Dict[str, Any]:
    from skystack.backends import select_backend

    paths = TargetPaths.from_config(config, target)
    if reference is None:
        reference = load_state(paths).get("reference", {}).get("reference")
        if not reference:
            raise RuntimeError("No reference yet -- run the 'reference' stage first.")
    reference = Path(reference)

    frames = calibrated_frames(paths, sessions)
    if not force:
        done = already_registered_stems(paths)
        frames = [f for f in frames if raw_stem(f) not in done]
    if not frames:
        logger.info("Nothing new to register for %s", target)
        return _record(paths, "register", {"registered": 0, "failed": []})["register"]

    backend = backend or select_backend(config)
    backend.start()
    try:
        outputs = backend.register(
            frames, reference, paths.registered_dir, postfix=REGISTERED_POSTFIX,
            distortion_correction=(config.get("registration", {}) or {}).get("distortion_correction", True),
        )
    finally:
        backend.stop()

    failed = [str(f) for f in frames if f not in outputs]
    if failed:
        logger.warning("%d of %d frames failed to register", len(failed), len(frames))
    return _record(paths, "register", {"reference": str(reference), "registered": len(outputs),
                                       "failed": failed, "sessions": sessions})["register"]


# ---------------------------------------------------------------------------
# Stage 4: measure frame quality (SubframeSelector) -- right after calibration
# ---------------------------------------------------------------------------

DB_PATH = SKYSTACK_DIR / "skystack.db"


def _open_db():
    from skystack_db import SkyStackDatabase

    return SkyStackDatabase(DB_PATH)


def _frame_header_fields(path: Path) -> Dict[str, Any]:
    try:
        hdr = read_header_info(path)
    except Exception:
        hdr = {}
    try:
        exptime = float(hdr.get("EXPTIME") or hdr.get("EXPOSURE"))
    except (TypeError, ValueError):
        exptime = None
    return {
        "filter_name": str(hdr["FILTER"]).strip() if hdr.get("FILTER") else None,
        "exposure_time": exptime,
        "date_obs": hdr.get("DATE-OBS"),
    }


def stage_measure(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]] = None,
    force: bool = False,
    backend=None,
) -> Dict[str, Any]:
    """Measure FWHM, eccentricity, star count, PSF signal weight, ... of the
    target's calibrated frames into skystack.db (frame_metrics)."""
    from skystack.backends import select_backend
    from skystack.frame_metrics import frame_records

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("measurement", {}) or {})
    frames = calibrated_frames(paths, sessions)
    if not force:
        with _open_db() as db:
            done = db.measured_stems(target)
        frames = [f for f in frames if raw_stem(f) not in done]
    if not frames:
        logger.info("Nothing new to measure for %s", target)
        return _record(paths, "measure", {"measured": 0, "failed": [], "sessions": sessions})["measure"]

    # Batches bound each PixInsight call and save progress as it goes.
    batch_size = max(1, int(cfg.get("batch_size", 200)))
    measured, failed = 0, []
    backend = backend or select_backend(config)
    backend.start()
    try:
        for start in range(0, len(frames), batch_size):
            batch = frames[start:start + batch_size]
            logger.info("Measuring frames %d-%d of %d", start + 1, start + len(batch), len(frames))
            results = backend.measure(batch, subframe_scale=float(cfg.get("subframe_scale", 1.0)),
                                      camera_gain=float(cfg.get("camera_gain", 1.0)))
            rows = [{"raw_stem": raw_stem(f), "file_path": str(f), **_frame_header_fields(f), **rec}
                    for f, rec in frame_records(results)]
            with _open_db() as db:
                db.upsert_frame_metrics(target, rows)
            measured += len(rows)
            failed += [str(f) for f in batch if f not in results]
    finally:
        backend.stop()

    if failed:
        logger.warning("%d of %d frames could not be measured", len(failed), len(frames))
    return _record(paths, "measure", {"measured": measured, "failed": failed,
                                      "sessions": sessions})["measure"]


def measured_frame_count(target: str, frames: Iterable[Path]) -> int:
    """How many of these frames have metrics (by raw stem) in skystack.db."""
    try:
        with _open_db() as db:
            done = db.measured_stems(target)
    except Exception:
        return 0
    return sum(raw_stem(f) in done for f in frames)


def _triage_metrics(target: str, frames: List[Path]):
    """({frame file name: {metric: value}}, metric spec) for the triage page's
    subframe selector. Metrics are looked up by raw stem, so measurements of
    the calibrated frames apply to their registered versions."""
    from skystack.frame_metrics import METRICS

    spec = [{"key": k, "label": m.label, "better": m.better} for k, m in METRICS.items() if m.selectable]
    try:
        with _open_db() as db:
            df = db.frame_metrics_frame(target)
    except Exception as e:  # metrics are a nice-to-have on the page
        logger.warning("Could not load frame metrics: %s", e)
        return {}, spec
    by_stem = {r["raw_stem"]: r for r in df.to_dict("records")}
    out = {}
    for f in frames:
        r = by_stem.get(raw_stem(f))
        if r:
            out[f.name] = {s["key"]: (r.get(s["key"]) if r.get(s["key"]) == r.get(s["key"]) else None)  # NaN -> None
                           for s in spec}
    if frames and not out:
        logger.warning("No frame metrics for %s -- run the 'measure' stage to get the subframe selector.", target)
    return out, spec


# ---------------------------------------------------------------------------
# Stage 7: triage -- send to reviewers, or review here / apply returned decisions
# ---------------------------------------------------------------------------

def _build_triage(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]],
    session_id: str,
    local: Optional[Dict[str, Any]] = None,
    scope: str = "pending",
    web: bool = False,
) -> Dict[str, Any]:
    from skystack.triage import build_triage_package

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("remote_triage", {}) or {})
    frames = triage_candidates(paths, scope, sessions)
    if not frames:
        what = "waiting for triage" if scope == "pending" else "registered"
        raise RuntimeError(f"No frames {what} for {target} under {paths.root}")

    safe_target = "".join(c if c.isalnum() or c in "-_" else "_" for c in target)
    html = paths.triage_dir / f"{safe_target}_triage_{session_id}.html"
    manifest_path = paths.triage_dir / f"{session_id}.manifest.json"
    # previews can be bigger when the page never leaves this machine
    on_this_pc = bool(local) and not web
    preview_size = int(cfg.get("local_preview_size", 2400) if on_this_pc else cfg.get("preview_size", 1200))
    metrics, spec = _triage_metrics(target, frames)
    build_triage_package(
        frames, paths.root, html, manifest_path, target,
        session_label=", ".join(sessions) if sessions else "all sessions",
        session_id=session_id,
        preview_size=preview_size,
        quality=int(cfg.get("jpeg_quality", 80)),
        workers=int(cfg.get("workers", 4)),
        metrics=metrics, metric_spec=spec, local=local,
    )
    return {"session_id": session_id, "package": str(html), "manifest": str(manifest_path),
            "n_frames": len(frames), "n_measured": len(metrics), "sessions": sessions, "scope": scope}


def stage_triage_send(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]] = None,
    deliver: bool = True,
    scope: str = "pending",
) -> Dict[str, Any]:
    """Build the self-contained triage page and send it to reviewers
    (outbox + Discord); their decisions files come back for apply-triage."""
    from skystack.delivery import deliver_package
    from skystack.triage import new_session_id

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("remote_triage", {}) or {})
    info = _build_triage(config, target, sessions, new_session_id(), scope=scope)
    delivery = {}
    if deliver:
        delivery = deliver_package(Path(info["package"]), cfg, target, info["n_frames"], info["session_id"])
    return _record(paths, "triage", {**info, "mode": "remote", "delivery": delivery,
                                     "applied": False})["triage"]


def stage_triage_web(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]] = None,
    then_argv: Optional[List[str]] = None,
    next_steps: str = "",
    scope: str = "pending",
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the triage page for review in someone's own browser (served by
    skystack/triage_server.py) and register it; returns the session entry,
    whose path is session_path(entry). Finishing the review there queues
    then_argv + [decisions file] (default: just apply the decisions)."""
    from skystack.triage import new_session_id
    from skystack.triage_server import register_session, session_path

    paths = TargetPaths.from_config(config, target)
    session_id = new_session_id()
    info = _build_triage(config, target, sessions, session_id, scope=scope, web=True,
                         local={"submit_url": "submit", "next_steps": next_steps or "applying the decisions"})
    entry = register_session(session_id, target, Path(info["package"]), paths.triage_dir / "decisions",
                             then_argv or ["apply-triage", "-t", target], next_steps, requested_by)
    _record(paths, "triage", {**info, "mode": "web", "web_path": session_path(entry),
                              "requested_by": requested_by, "applied": False})
    logger.info("Web review ready: %s (%d frames)", session_path(entry), info["n_frames"])
    return entry


def stage_triage_local(
    config: Mapping[str, Any],
    target: str,
    sessions: Optional[List[str]] = None,
    next_steps: str = "",
    open_window: bool = True,
    timeout_s: Optional[float] = None,
    scope: str = "pending",
) -> Path:
    """Open the triage tool on this machine and wait for "Finish review".
    Returns the saved decisions file (ready for stage_apply_triage)."""
    from skystack.local_review import ReviewServer, open_app_window
    from skystack.triage import DECISIONS_FORMAT, new_session_id

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("remote_triage", {}) or {})
    session_id = new_session_id()
    built: Dict[str, Any] = {}

    def html_for(base_url: str) -> bytes:
        built.update(_build_triage(config, target, sessions, session_id,
                                   local={"submit_url": base_url + "/submit", "next_steps": next_steps},
                                   scope=scope))
        return Path(built["package"]).read_bytes()

    def validate(data: Dict[str, Any]) -> None:
        if data.get("format") != DECISIONS_FORMAT or data.get("session_id") != session_id:
            raise ValueError("These decisions are not for this review session.")

    server = ReviewServer(html_for, validate)
    _record(paths, "triage", {**built, "mode": "local", "applied": False})
    logger.info("Triage tool: %s -- finish the review there (Finish review) to continue.", server.base_url)
    if open_window:
        open_app_window(server.base_url, str(cfg.get("local_browser", "auto")))
    decisions = server.serve_until_submitted(timeout_s)
    if decisions is None:
        raise RuntimeError("The review wasn't finished (timed out waiting for Finish review).")

    out = paths.triage_dir / "decisions" / f"{session_id}_local.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    logger.info("Review finished: %d of %d frames selected (%d rejected by metric cuts).",
                decisions.get("kept_count", 0), decisions.get("total", 0),
                len(decisions.get("metric_rejected", [])))
    return out


def stage_apply_triage(
    config: Mapping[str, Any],
    target: str,
    decision_files: List[Path],
    manifest: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    from skystack.triage import apply_decisions

    paths = TargetPaths.from_config(config, target)
    state = load_state(paths)
    if manifest is None:
        manifest = state.get("triage", {}).get("manifest")
        if not manifest:
            raise RuntimeError("No triage package on record -- pass --manifest explicitly.")
    delete = bool((config.get("remote_triage", {}) or {}).get("delete_rejected", False))

    summary = apply_decisions(
        Path(manifest), decision_files, keep_dir=paths.blinked_dir,
        reject_dir=None if delete else paths.rejected_dir,
        delete_rejected=delete, dry_run=dry_run,
    )
    logger.info("Triage applied: %d kept, %d rejected, %d missing (reviewers: %s)",
                summary["kept"], summary["rejected"], summary["missing"], ", ".join(summary["reviewers"]))
    if not dry_run:
        triage = {**state.get("triage", {}), "applied": True, "manifest": str(manifest),
                  "kept": summary["kept"], "rejected": summary["rejected"],
                  "metric_rejected": summary["metric_rejected"], "metric_cuts": summary["metric_cuts"],
                  "reviewers": summary["reviewers"]}
        state["triage"] = triage
        save_state(paths, state)
    return summary


# ---------------------------------------------------------------------------
# Stage 8: local normalization + integration
# ---------------------------------------------------------------------------

def choose_rejection(n_frames: int, thresholds: Optional[Mapping[str, int]] = None) -> str:
    """Rejection algorithm by frame count, mirroring WBPP's auto choice."""
    t = {"percentile_below": 8, "winsorized_below": 20, **(thresholds or {})}
    if n_frames < t["percentile_below"]:
        return "PercentileClip"
    if n_frames < t["winsorized_below"]:
        return "WinsorizedSigmaClip"
    return "LinearFit"


def _fmt_num(v, fmt="{:.2f}"):
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return str(v)


def group_for_stacking(frames: Iterable[Path]) -> Dict[tuple, List[Path]]:
    groups: Dict[tuple, List[Path]] = defaultdict(list)
    for f in frames:
        hdr = read_header_info(f)
        key = (
            str(hdr.get("FILTER") or "NOFILTER").strip(),
            _fmt_num(hdr.get("EXPTIME") or hdr.get("EXPOSURE")),
            _fmt_num(hdr.get("XBINNING") or 1, "{:.0f}"),
        )
        groups[key].append(f)
    return dict(groups)


def stage_stack(config: Mapping[str, Any], target: str, backend=None) -> List[Dict[str, Any]]:
    from skystack.backends import select_backend

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("stacking", {}) or {})
    min_frames = int(cfg.get("min_frames", 3))

    frames = _images_under(paths.blinked_dir, exclude_dirs=())
    if not frames:
        raise RuntimeError(f"No triaged frames in {paths.blinked_dir} -- apply triage decisions first.")

    groups, skipped = [], []
    for (filt, exptime, binning), files in sorted(group_for_stacking(frames).items()):
        name = f"BIN-{binning}_EXPOSURE-{exptime}s_FILTER-{filt}"
        if len(files) < min_frames:
            logger.warning("Skipping %s: only %d frames (need %d)", name, len(files), min_frames)
            skipped.append({"name": name, "status": "skipped", "n_frames": len(files)})
            continue
        out = paths.master_dir / f"masterLight_{name}.xisf"
        if out.exists():
            superseded = paths.master_dir / "superseded"
            superseded.mkdir(parents=True, exist_ok=True)
            out.replace(superseded / f"{out.stem}_{datetime.now():%Y%m%d%H%M%S}{out.suffix}")
        groups.append({
            "name": name, "files": files, "output_path": out,
            "rejection": choose_rejection(len(files), cfg.get("rejection_thresholds")),
            "filter": filt, "exptime": exptime,
        })

    results: List[Dict[str, Any]] = []
    if groups:
        backend = backend or select_backend(config)
        backend.start()
        try:
            results = backend.integrate(groups, paths.ln_dir, ln_scale=int(cfg.get("ln_scale", 1024)))
        finally:
            backend.stop()

    by_name = {g["name"]: g for g in groups}
    for r in results:
        g = by_name.get(r["name"], {})
        r.update({"filter": g.get("filter"), "exptime": g.get("exptime"),
                  "rejection": g.get("rejection")})
    results += skipped
    (paths.root / "integration_summary.json").write_text(json.dumps(results, indent=2, default=str))
    _record(paths, "stack", {"groups": results})
    for r in results:
        logger.info("  %-40s %s", r["name"], r.get("status"))
    return results


# ---------------------------------------------------------------------------
# Stage 8b: post-processing of the integrated masters (BlurXTerminator, ...)
# ---------------------------------------------------------------------------

def stacked_masters(paths: TargetPaths) -> List[Path]:
    """Current masterLights (superseded ones live in a subfolder and are left out)."""
    return sorted(p for p in paths.master_dir.glob("masterLight*")
                  if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def stage_postprocess(
    config: Mapping[str, Any],
    target: str,
    steps: Optional[List[str]] = None,
    masters: Optional[List[str]] = None,
    backend=None,
) -> List[Dict[str, Any]]:
    """Apply post-processing presets (skystack/postprocess.py) in order to the
    target's masterLights, writing new files to REGISTERED/<target>/processed/.

    masters: file names (or stems) in master/ to process; default all current ones.
    """
    from skystack.backends import select_backend
    from skystack.postprocess import output_name, resolve_steps

    paths = TargetPaths.from_config(config, target)
    resolved = resolve_steps(config, steps)
    available = stacked_masters(paths)
    if masters:
        wanted = set(masters)
        chosen = [m for m in available if m.name in wanted or m.stem in wanted]
        missing = wanted - {m.name for m in chosen} - {m.stem for m in chosen}
        if missing:
            raise FileNotFoundError(f"Not in {paths.master_dir}: {sorted(missing)}")
    else:
        chosen = available
    if not chosen:
        raise RuntimeError(f"No masterLight files in {paths.master_dir} -- run the 'stack' stage first.")

    items = [{"input": m, "output": paths.processed_dir / output_name(m.stem, resolved)} for m in chosen]
    logger.info("Post-processing %d masters with: %s", len(items), " -> ".join(s["name"] for s in resolved))
    backend = backend or select_backend(config)
    backend.start()
    try:
        results = backend.postprocess(items, resolved)
    finally:
        backend.stop()

    for r in results:
        logger.info("  %-50s %s", Path(r["input"]).name, r.get("status") if r.get("status") == "ok"
                    else f"{r.get('status')}: {r.get('error')}")
    _record(paths, "postprocess", {"steps": [s["name"] for s in resolved], "results": results})
    return results


def is_postprocess_after_stack(config: Mapping[str, Any]) -> bool:
    return bool((config.get("postprocess", {}) or {}).get("after_stack", False))


def postprocess_default_steps(config: Mapping[str, Any]) -> List[str]:
    from skystack.postprocess import default_steps

    return default_steps(config)


def default_triage_mode(config: Mapping[str, Any]) -> str:
    """local (review here, then carry on) | send (to reviewers) | build (page only)."""
    return str((config.get("remote_triage", {}) or {}).get("mode", "send")).lower()


# ---------------------------------------------------------------------------
# Stage 9: client export
# ---------------------------------------------------------------------------

def processed_masters(paths: TargetPaths) -> List[Path]:
    """Post-processed (e.g. BlurXTerminator-corrected) masters in processed/."""
    return sorted(p for p in paths.processed_dir.glob("*")
                  if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def is_client_target(config: Mapping[str, Any], target: str) -> bool:
    clients = (config.get("client_export", {}) or {}).get("client_targets") or []
    return target in clients


def stage_export(
    config: Mapping[str, Any],
    target: str,
    include_raw_lights: Optional[str] = None,
    include_calibration_masters: Optional[bool] = None,
    sessions: Optional[List[str]] = None,
    include_processed: Optional[bool] = None,
) -> Path:
    from skystack.client_export import (
        calibration_masters_for, export_client_zip, select_raw_lights,
    )

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("client_export", {}) or {})
    raw_mode = include_raw_lights or cfg.get("include_raw_lights", "none")
    with_cal = cfg.get("include_calibration_masters", False) if include_calibration_masters is None \
        else include_calibration_masters

    masters = stacked_masters(paths)
    if include_processed is None:
        include_processed = bool(cfg.get("include_processed", True))
    processed = processed_masters(paths) if include_processed else []
    if include_processed and not processed:
        logger.warning("No post-processed masters in %s to include.", paths.processed_dir)
    if not masters:
        raise RuntimeError(f"No masterLight files in {paths.master_dir} -- run the 'stack' stage first.")

    summary_path = paths.root / "integration_summary.json"
    integration = json.loads(summary_path.read_text()) if summary_path.exists() else None

    raws = select_raw_lights(raw_mode, paths.light_dir, _images_under(paths.blinked_dir, ()), sessions)
    cal = calibration_masters_for(raws, paths.calibrated_dir / "calibration_log.json") if (with_cal and raws) else set()
    if with_cal and not raws:
        logger.warning("Calibration masters are only exported alongside raw lights; none selected.")

    out_dir = Path(cfg.get("output_dir") or config.get("shared_dir"))
    out_zip = out_dir / target / f"{target}_{datetime.now():%Y%m%d}.zip"
    export_client_zip(
        target, masters, out_zip, integration=integration, processed=processed,
        raw_lights=raws, raw_root=paths.light_dir, calibration_masters=cal,
        master_format=cfg.get("master_format", "fits"), compress=bool(cfg.get("compress", True)),
    )
    _record(paths, "export", {"zip": str(out_zip), "raw_lights": raw_mode, "n_raw": len(raws),
                              "n_processed": len(processed),
                              "n_calibration_masters": len(cal)})
    return out_zip


def stage_add_processed(
    config: Mapping[str, Any],
    target: str,
    zip_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add the post-processed masters to an existing client zip, without
    rebuilding it (default: the last export on record for the target).
    Files already in the zip under the same name are replaced."""
    from skystack.client_export import add_to_client_zip

    paths = TargetPaths.from_config(config, target)
    cfg = dict(config.get("client_export", {}) or {})
    state = load_state(paths)
    if zip_path is None:
        zip_path = (state.get("export") or {}).get("zip")
        if not zip_path:
            raise RuntimeError("No client export on record for this target -- run 'export' first, "
                               "or pass the zip explicitly.")
    zip_path = Path(zip_path)
    processed = processed_masters(paths)
    if not processed:
        raise RuntimeError(f"No post-processed masters in {paths.processed_dir} -- run 'postprocess' first.")

    added = add_to_client_zip(zip_path, target, processed, folder="processed",
                              master_format=cfg.get("master_format", "fits"),
                              compress=bool(cfg.get("compress", True)))
    export = dict(state.get("export") or {})
    if Path(export.get("zip", "")) == zip_path:
        export["n_processed"] = len(processed)
        export["processed_added"] = datetime.now().isoformat(timespec="seconds")
        state["export"] = export
        save_state(paths, state)
    return {"zip": str(zip_path), "added": added}
