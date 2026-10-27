"""Bundle a target's results into a zip for a client.

Always included: the integrated masterLight files (optionally converted to
FITS), their post-processed copies from processed/ (unless
client_export.include_processed is false), and a summary of what went into
each one. Optionally included:

- the raw LIGHT frames -- either only those that survived triage ("kept")
  or every raw frame of the target/session ("all");
- the master flats/darks that calibrated those raw frames, looked up in
  CALIBRATED/<target>/calibration_log.json, so the client can redo the
  calibration themselves.
"""
from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from astro_utils.fits_helpers import parse_date_from_header, read_header_info

logger = logging.getLogger(__name__)

RAW_LIGHT_CHOICES = ("none", "kept", "all")
IMAGE_SUFFIXES = {".fits", ".fit", ".fts", ".xisf"}


def raw_stem_of(processed: Path) -> str:
    """Raw LIGHT stem for a calibrated/registered derivative.

    Calibration appends "__DDMMYYYY_c" and registration "_r", so the raw
    stem is everything before the last "__".
    """
    stem = Path(processed).stem
    return stem.rsplit("__", 1)[0] if "__" in stem else stem


def find_raw_lights(light_dir: Path, sessions: Optional[Iterable[str]] = None) -> List[Path]:
    light_dir = Path(light_dir)
    roots = [light_dir / s for s in sessions] if sessions else [light_dir]
    out = []
    for root in roots:
        out += [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    return sorted(out)


def select_raw_lights(
    mode: str,
    light_dir: Path,
    kept_frames: Iterable[Path],
    sessions: Optional[Iterable[str]] = None,
) -> List[Path]:
    if mode not in RAW_LIGHT_CHOICES:
        raise ValueError(f"include_raw_lights must be one of {RAW_LIGHT_CHOICES}")
    if mode == "none":
        return []
    raws = find_raw_lights(light_dir, sessions)
    if mode == "all":
        return raws
    kept_stems = {raw_stem_of(p) for p in kept_frames}
    selected = [p for p in raws if p.stem in kept_stems]
    missing = len(kept_stems) - len({p.stem for p in selected})
    if missing:
        logger.warning("%d kept frames have no matching raw LIGHT file", missing)
    return selected


def _expt_key_matches(key: str, exptime) -> bool:
    try:
        return abs(float(key) - float(exptime)) < 1e-3
    except (TypeError, ValueError):
        return False


def calibration_masters_for(raw_lights: Iterable[Path], calibration_log: Path) -> Set[Path]:
    """Master flats/darks the calibration log recorded for these raw frames."""
    if not Path(calibration_log).exists():
        logger.warning("No calibration log at %s; no calibration masters included", calibration_log)
        return set()
    log = json.loads(Path(calibration_log).read_text())

    masters: Set[Path] = set()
    seen = set()
    for raw in raw_lights:
        hdr = read_header_info(raw)
        date_obs = parse_date_from_header(hdr=hdr)
        if date_obs is None:
            continue
        key = (date_obs.strftime("%d-%m-%Y"), hdr.get("FILTER"), hdr.get("EXPTIME"))
        if key in seen:
            continue
        seen.add(key)
        by_filter = log.get(key[0], {}).get(str(key[1]), {})
        for expt_key, entry in by_filter.items():
            if isinstance(entry, dict) and _expt_key_matches(expt_key, key[2]):
                for kind in ("flat_master", "dark_master"):
                    if entry.get(kind):
                        masters.add(Path(entry[kind]))
    return masters


def write_summary(integration: List[Mapping[str, Any]], target: str) -> str:
    lines = [f"SkyStack integration summary - {target}",
             f"Generated {datetime.now():%Y-%m-%d %H:%M}", "",
             "FILTER,EXPOSURE (s),FRAMES,REJECTION,INTEGRATION (hrs),MASTER"]
    total = 0.0
    for g in integration:
        if g.get("status") != "ok":
            continue
        hrs = float(g.get("exptime") or 0) * int(g.get("n_frames") or 0) / 3600.0
        total += hrs
        lines.append(f"{g.get('filter')},{g.get('exptime')},{g.get('n_frames')},"
                     f"{g.get('rejection')},{hrs:.2f},{Path(g['output_path']).name}")
    lines += ["", f"TOTAL INTEGRATION: {total:.2f} hrs"]
    return "\n".join(lines) + "\n"


def _master_for_zip(m: Path, target: str, folder: str, master_format: str, tmp: Path):
    """(file to write, name inside the zip), converting XISF to FITS if asked."""
    from astro_utils.fits_helpers import xisf2fits

    if master_format == "fits" and m.suffix.lower() == ".xisf":
        converted = tmp / (m.stem + ".fits")
        xisf2fits(m, converted, delete_after_convert=False)
        m = converted
    return m, f"{target}/{folder}/{m.name}"


def add_to_client_zip(
    zip_path: Path,
    target: str,
    files: Iterable[Path],
    folder: str = "processed",
    master_format: str = "xisf",
    compress: bool = True,
) -> List[str]:
    """Add files to an existing client zip under <target>/<folder>/.

    A file whose name is already in the zip replaces it (the zip is
    rewritten once without the stale entries); otherwise files are appended.
    Returns the names written.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"Client zip not found: {zip_path}")
    compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED

    with tempfile.TemporaryDirectory(prefix="skystack_export_") as tmp:
        prepared = [_master_for_zip(Path(f), target, folder, master_format, Path(tmp)) for f in files]
        new_names = {arc for _, arc in prepared}
        with zipfile.ZipFile(zip_path) as zf:
            stale = new_names & set(zf.namelist())

        if stale:
            rewritten = zip_path.with_suffix(".rewrite.tmp")
            with zipfile.ZipFile(zip_path) as src, \
                    zipfile.ZipFile(rewritten, "w", compression=compression) as dst:
                for info in src.infolist():
                    if info.filename not in stale:
                        with src.open(info) as fh:
                            dst.writestr(info, fh.read(), compress_type=info.compress_type)
            rewritten.replace(zip_path)
            logger.info("Replacing %d existing entries in %s", len(stale), zip_path.name)

        with zipfile.ZipFile(zip_path, "a", compression=compression,
                             compresslevel=1 if compress else None) as zf:
            for src, arcname in prepared:
                zf.write(src, arcname)

    logger.info("Added %d files to %s", len(prepared), zip_path)
    return sorted(new_names)


def export_client_zip(
    target: str,
    masters: Iterable[Path],
    out_zip: Path,
    integration: Optional[List[Mapping[str, Any]]] = None,
    raw_lights: Iterable[Path] = (),
    raw_root: Optional[Path] = None,
    calibration_masters: Iterable[Path] = (),
    master_format: str = "xisf",
    compress: bool = True,
    processed: Iterable[Path] = (),
) -> Path:
    """Write the zip. Paths inside it are rooted at <target>/."""
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED

    with tempfile.TemporaryDirectory(prefix="skystack_export_") as tmp, \
            zipfile.ZipFile(out_zip, "w", compression=compression, compresslevel=1 if compress else None) as zf:
        for folder, files in (("masters", masters), ("processed", processed)):
            for m in files:
                src, arcname = _master_for_zip(Path(m), target, folder, master_format, Path(tmp))
                zf.write(src, arcname)

        if integration:
            zf.writestr(f"{target}/integration_summary.csv", write_summary(integration, target))

        raw_lights = list(raw_lights)
        for raw in raw_lights:
            rel = raw.relative_to(raw_root) if raw_root else Path(raw.name)
            zf.write(raw, f"{target}/raw_lights/{rel.as_posix()}")

        for cal in sorted(set(calibration_masters)):
            kind = "darks" if "dark" in cal.name.lower() or "DARK" in cal.parts else "flats"
            if not cal.exists():
                logger.warning("Calibration master missing, not exported: %s", cal)
                continue
            zf.write(cal, f"{target}/calibration_masters/{kind}/{cal.name}")

    logger.info("Client export written: %s (%.1f MB)", out_zip, out_zip.stat().st_size / 1e6)
    return out_zip
