"""Remote triage of registered lights.

build_triage_package() turns a set of registered frames into ONE
self-contained HTML file (auto-stretched JPEG previews embedded) that
anyone can open in a browser -- no Python, no install. The reviewer blinks
through the frames with the keyboard, marks rejects, can set subframe-selector
cuts on the measured metrics (FWHM, eccentricity, stars, ...), and downloads a
small decisions JSON to send back. Reviewing on this machine instead,
skystack.local_review serves the same page and takes the decisions directly.

Alongside the HTML, a manifest JSON is written locally (it never leaves this
machine). It maps each preview's key back to the full-resolution file and
carries the session id, so apply_decisions() can check a returned decisions
file belongs to this package before moving anything.

Because the frames are already registered onto a common canvas, stars stay
put while blinking, so trailing, clouds, satellites and focus drift stand
out immediately.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from astro_utils.fits_helpers import read_header_info

logger = logging.getLogger(__name__)

DECISIONS_FORMAT = "SkyStack triage decisions"
MANIFEST_FORMAT = "SkyStack triage manifest"
TEMPLATE_PATH = Path(__file__).parent / "triage_template.html"


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------

def _encode_jpeg(img, quality: int) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_preview(path: Path, preview_size: int, thumb_size: int, quality: int) -> Dict[str, str]:
    """Auto-stretched preview + thumbnail as base64 JPEGs.

    Reuses QuickBlink's loader and PixInsight-style STF stretch so a frame
    looks the same here as in QuickBlink.
    """
    from QuickBlink import auto_stretch, downsample_linear, downsample_preview, load_image

    data = load_image(Path(path))
    small = downsample_linear(data, round(preview_size * 1.2))
    img = downsample_preview(auto_stretch(small), preview_size)
    thumb = img.copy()
    thumb.thumbnail((thumb_size, thumb_size))
    return {"img": _encode_jpeg(img, quality), "thumb": _encode_jpeg(thumb, 70)}


def _frame_info(path: Path) -> Dict[str, Any]:
    try:
        hdr = read_header_info(Path(path))
    except Exception:
        hdr = {}
    return {
        "filter": hdr.get("FILTER"),
        "exptime": hdr.get("EXPTIME") or hdr.get("EXPOSURE"),
        "date": hdr.get("DATE-OBS"),
    }


# ---------------------------------------------------------------------------
# Package creation
# ---------------------------------------------------------------------------

def build_triage_package(
    frames: Iterable[Path],
    root: Path,
    out_html: Path,
    manifest_path: Path,
    target: str,
    session_label: str = "",
    session_id: Optional[str] = None,
    preview_size: int = 1200,
    thumb_size: int = 160,
    quality: int = 80,
    workers: int = 4,
    metrics: Optional[Dict[str, Dict[str, Any]]] = None,
    metric_spec: Optional[List[Dict[str, Any]]] = None,
    local: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write the triage HTML and its local manifest. Returns the manifest.

    metrics: optional {frame file name: {metric: value}} for the page's
    subframe selector; metric_spec: [{"key", "label", "better"}] describing
    them. local: {"submit_url", "next_steps"} when the page is served by
    skystack.local_review (finishing posts the decisions back instead of
    downloading them).

    Keys are paths relative to `root` (posix style), which keeps them
    readable in the decisions file without exposing local drive layout.
    """
    root = Path(root)
    frames = sorted(Path(f) for f in frames)
    if not frames:
        raise ValueError("No frames to triage")
    session_id = session_id or new_session_id()

    def _one(path: Path):
        try:
            return path, render_preview(path, preview_size, thumb_size, quality), None
        except Exception as e:  # keep going; the reviewer sees an error tile
            logger.warning("Preview failed for %s: %s", path.name, e)
            return path, None, repr(e)

    records: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (path, preview, error) in enumerate(pool.map(_one, frames), start=1):
            logger.info("[%d/%d] preview %s", i, len(frames), path.name)
            records.append({
                "key": path.relative_to(root).as_posix(),
                "name": path.name,
                **_frame_info(path),
                "metrics": (metrics or {}).get(path.name),
                "img": preview["img"] if preview else None,
                "thumb": preview["thumb"] if preview else None,
                "error": error,
            })

    payload = {
        "format": "SkyStack triage package",
        "version": 1,
        "session_id": session_id,
        "target": target,
        "session_label": session_label,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frames": records,
        "metric_spec": metric_spec or [],
        "local": local,
    }
    data_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace("__TITLE__", _html_escape(f"Triage · {target}"))
    html = html.replace("/*__DATA__*/null", data_json)

    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")

    manifest = {
        "format": MANIFEST_FORMAT,
        "version": 1,
        "session_id": session_id,
        "target": target,
        "session_label": session_label,
        "created_utc": payload["created_utc"],
        "root": str(root),
        "package": str(out_html),
        "frames": {r["key"]: str(root / r["key"]) for r in records},
    }
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    size_mb = out_html.stat().st_size / 1e6
    logger.info("Triage package %s: %d frames, %.1f MB", out_html.name, len(records), size_mb)
    return manifest


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Applying returned decisions
# ---------------------------------------------------------------------------

def load_decisions(paths: Iterable[Path], session_id: str) -> Dict[str, Any]:
    """Merge one or more reviewers' decision files.

    A frame is rejected if ANY reviewer rejected it -- a second pair of eyes
    can only remove frames, never resurrect one someone else flagged.
    """
    discarded: set = set()
    metric_rejected: set = set()
    metric_cuts: List[Dict[str, Any]] = []
    reviewers: List[str] = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        if data.get("format") != DECISIONS_FORMAT:
            raise ValueError(f"{p} is not a SkyStack triage decisions file")
        if data.get("session_id") != session_id:
            raise ValueError(
                f"{p} is for triage session {data.get('session_id')!r}, "
                f"but this manifest is for {session_id!r}"
            )
        discarded.update(data.get("discarded", []))
        metric_rejected.update(data.get("metric_rejected", []))   # version 2+
        if data.get("metric_cuts"):
            metric_cuts.append({"reviewer": data.get("reviewer") or Path(p).name, "cuts": data["metric_cuts"]})
        reviewers.append(data.get("reviewer") or Path(p).name)
    return {"discarded": discarded, "metric_rejected": metric_rejected & discarded,
            "metric_cuts": metric_cuts, "reviewers": reviewers}


def apply_decisions(
    manifest_path: Path,
    decision_paths: Iterable[Path],
    keep_dir: Path,
    reject_dir: Optional[Path] = None,
    delete_rejected: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Move kept frames to keep_dir and rejects to reject_dir (or delete them).

    Registered frames are derivatives of untouched raw LIGHTs, so deleting
    a reject loses nothing that can't be regenerated -- but moving them to
    reject_dir is the default so a decision can still be reversed.
    """
    from QuickBlink import _safe_move

    if delete_rejected and reject_dir is not None:
        raise ValueError("Specify either reject_dir or delete_rejected, not both.")

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"{manifest_path} is not a SkyStack triage manifest")
    decisions = load_decisions(decision_paths, manifest["session_id"])

    unknown = decisions["discarded"] - set(manifest["frames"])
    if unknown:
        logger.warning("Ignoring %d rejected keys not in this package: %s",
                       len(unknown), sorted(unknown)[:5])

    summary = {"kept": 0, "rejected": 0, "missing": 0, "moved": [], "deleted": [],
               "reviewers": decisions["reviewers"],
               "metric_rejected": len(decisions["metric_rejected"] & set(manifest["frames"])),
               "metric_cuts": decisions["metric_cuts"]}
    for key, path_str in manifest["frames"].items():
        src = Path(path_str)
        rejected = key in decisions["discarded"]
        summary["rejected" if rejected else "kept"] += 1
        if not src.exists():
            summary["missing"] += 1
            continue
        if dry_run:
            continue
        if not rejected:
            dest_dir = Path(keep_dir)
        elif not delete_rejected and reject_dir is not None:
            dest_dir = Path(reject_dir)
        else:
            dest_dir = None
        if dest_dir is not None and src.parent.resolve() == dest_dir.resolve():
            continue   # re-triage: already where this decision puts it
        if not rejected:
            summary["moved"].append(str(_safe_move(src, Path(keep_dir) / src.name)))
        elif delete_rejected:
            src.unlink()
            summary["deleted"].append(str(src))
        elif reject_dir is not None:
            summary["moved"].append(str(_safe_move(src, Path(reject_dir) / src.name)))
    return summary
