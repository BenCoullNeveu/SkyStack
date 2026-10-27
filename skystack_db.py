#!/usr/bin/env python3
"""
SkyStack Database Manager
=========================
SQLite-backed status tracking for astrophotography targets.

The core of this module is `compute_target_stats()`: a pure, filesystem-
and-JSON-based scan that answers "what's the processing status of this
target?" without opening a single FITS file. That matters because a
single target directory can hold 1000+ multi-megabyte frames -- reading
FITS headers for all of them on every refresh would be far too slow for a
UI "rescan" button. Instead it counts files by convention:

- total_lights / nights: every *.fits under LIGHT/<target>/**, and the
  count of session subdirectories directly under it.
- blinked_lights / rejected_lights / blink_yield_pct: raw-frame-stage review
  (triage the LIGHTs before calibrating). Frames that live under a
  "BLINKED" or "REJECTED"-named folder within LIGHT/<target>/, and the
  accept rate among reviewed frames. This is the older workflow style
  (e.g. M 106's history) -- see calibrated_blinked_frames/true_yield_pct
  below for the current preferred workflow.
- calibration_frames_used / calibrated_frames: read from the
  calibration_log.json that calibrate_images.py already writes, plus a
  live count of "*_c.fits"/"*_c.xisf" outputs.
- calibrated_blinked_frames / true_yield_pct: the current preferred
  workflow -- calibrate everything first, then triage the CALIBRATED
  frames (QuickBlink.py against CALIBRATED/<target>/, survivors moved to
  CALIBRATED/<target>/BLINKED/). Because a calibrated frame is a disposable
  derivative of its untouched raw LIGHT, a reject here can just be deleted
  outright rather than kept in a recoverable folder -- see QuickBlink.py's
  `apply --delete-discarded`. true_yield_pct is the genuine end-to-end
  yield: calibrated_blinked_frames / total_lights, i.e. what fraction of
  everything you captured actually made it to the stack-ready set.
- registered_frames / stacked_masters: counted from the REGISTERED/<target>
  tree PixInsight's registration step produces.
- run_routine.py's pipeline triages *registered* frames instead (survivors
  in REGISTERED/<target>/BLINKED/). Those count toward
  calibrated_blinked_frames, and so toward true_yield_pct, too.

The richer per-frame tables (light_frames, calibration_frames,
master_calibrations) are kept for future fine-grained work, with their
schema bugs fixed, but the target-level stats above are the primary,
tested path and what the UI reads.
"""

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProcessingStatus(Enum):
    """Processing pipeline stages for a single light or calibration frame."""
    MOVED_LOCALLY = "moved_locally"
    ACQUIRED = "acquired"
    BLINKED = "blinked"       # survived manual PixInsight Blink review
    CALIBRATED = "calibrated"
    REGISTERED = "registered"
    STACKED = "stacked"
    FAILED = "failed"          # DB row exists but the file is gone from disk
    USED = "used"               # calibration frame consumed into a master


class ProcessType(Enum):
    """Types of processing operations logged in processing_logs."""
    MOVE_LOCALLY = "move_locally"
    CALIBRATION = "calibration"
    REGISTRATION = "registration"
    STACKING = "stacking"
    DATABASE_SYNC = "database_sync"


@dataclass
class TargetInfo:
    """Per-target aggregate status, as shown in the UI."""
    id: Optional[int] = None
    name: str = ""
    total_lights: int = 0
    nights: int = 0
    blinked_lights: int = 0
    rejected_lights: int = 0
    unreviewed_lights: int = 0
    blink_yield_pct: Optional[float] = None
    calibration_frames_used: int = 0
    calibrated_frames: int = 0
    calibrated_blinked_frames: int = 0
    true_yield_pct: Optional[float] = None
    registered_frames: int = 0
    stacked_masters: int = 0
    last_scanned: Optional[str] = None
    last_modified: str = ""
    created_date: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class LightFrame:
    """A single light frame, for future fine-grained tracking."""
    id: Optional[int] = None
    target_id: int = 0
    local_file_path: str = ""
    remote_file_path: Optional[str] = None
    filter_name: str = ""
    exposure_time: float = 0.0
    gain: int = 0
    offset: int = 0
    temperature: float = 0.0
    rotation: float = 0.0
    date_obs: str = ""
    obs_night: str = ""
    status: str = ProcessingStatus.ACQUIRED.value
    quality_score: Optional[float] = None
    calibration_log: Optional[str] = None
    master_flat_id: Optional[int] = None
    master_dark_id: Optional[int] = None
    header_data: str = "{}"
    file_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class CalibrationFrame:
    """A single flat/dark/bias sub-frame, for future fine-grained tracking."""
    id: Optional[int] = None
    frame_type: str = ""  # 'FLAT', 'DARK', or 'BIAS'
    local_file_path: str = ""
    remote_file_path: Optional[str] = None
    filter_name: Optional[str] = None
    exposure_time: Optional[float] = None
    gain: int = 0
    offset: int = 0
    temperature: Optional[float] = None
    rotation: Optional[float] = None
    date_obs: str = ""
    obs_night: str = ""
    status: str = ProcessingStatus.ACQUIRED.value

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class MasterCalibration:
    """A stacked master flat/dark/bias."""
    id: Optional[int] = None
    frame_type: str = ""  # 'FLAT', 'DARK', or 'BIAS'
    file_path: str = ""
    filter_name: Optional[str] = None
    exposure_time: Optional[float] = None
    gain: int = 0
    offset: int = 0
    temperature: Optional[float] = None
    rotation: Optional[float] = None
    creation_date: str = ""
    num_subs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


## ============== Filesystem/JSON-based target stats ============== ##

def _is_under_named_dir(path: Path, name: str) -> bool:
    name = name.lower()
    return any(part.lower() == name for part in path.parts)


def compute_target_stats(
    target_name: str,
    light_base_dir: Path,
    calibrated_base_dir: Path,
    registered_base_dir: Path,
) -> Dict[str, Any]:
    """
    Compute one target's aggregate status purely from directory listings
    and calibration_log.json -- no FITS headers are opened. Safe to call
    for every target on every UI refresh, even with thousands of frames.

    blink_yield_pct is the true accept rate *among reviewed frames*:
    blinked / (blinked + rejected). It is None until at least one frame has
    been reviewed. Frames still sitting unreviewed in the raw LIGHT folder
    don't count against it either way, so this isn't diluted by a backlog
    of frames nobody has looked at yet.

    Historically, PixInsight's manual Blink process just deleted rejected
    frames with no trace, so rejected_lights (and therefore this yield) is
    only meaningful going forward, once QuickBlink.py's `apply` step -- or
    any process that moves rejects into a "REJECTED" folder instead of
    deleting them -- is used. For older, already-reviewed sessions with no
    REJECTED folder, this stays None even though every frame was in fact
    reviewed; there's no way to recover that history from disk alone.
    """
    stats: Dict[str, Any] = {
        "total_lights": 0,
        "nights": 0,
        "blinked_lights": 0,
        "rejected_lights": 0,
        "unreviewed_lights": 0,
        "blink_yield_pct": None,
        "calibration_frames_used": 0,
        "calibrated_frames": 0,
        "calibrated_blinked_frames": 0,
        "true_yield_pct": None,
        "registered_frames": 0,
        "stacked_masters": 0,
    }

    target_dir = Path(light_base_dir) / target_name
    if target_dir.exists():
        light_files = [p for p in target_dir.rglob("*.fits") if p.is_file()]
        stats["total_lights"] = len(light_files)
        stats["blinked_lights"] = sum(
            1 for p in light_files if _is_under_named_dir(p, "blinked")
        )
        stats["rejected_lights"] = sum(
            1 for p in light_files if _is_under_named_dir(p, "rejected")
        )
        stats["unreviewed_lights"] = (
            stats["total_lights"] - stats["blinked_lights"] - stats["rejected_lights"]
        )
        reviewed = stats["blinked_lights"] + stats["rejected_lights"]
        if reviewed > 0:
            stats["blink_yield_pct"] = round(100.0 * stats["blinked_lights"] / reviewed, 1)
        stats["nights"] = sum(1 for d in target_dir.iterdir() if d.is_dir())

    calib_dir = Path(calibrated_base_dir) / target_name
    if calib_dir.exists():
        stats["calibrated_frames"] = sum(
            1
            for p in calib_dir.rglob("*_c.*")
            if p.is_file() and p.suffix.lower() in (".fits", ".xisf")
        )

        blinked_calib_dir = calib_dir / "BLINKED"
        if blinked_calib_dir.exists():
            stats["calibrated_blinked_frames"] = sum(
                1
                for p in blinked_calib_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in (".fits", ".xisf")
            )

        log_path = calib_dir / "calibration_log.json"
        if log_path.exists():
            try:
                log_data = json.loads(log_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not read calibration log for {target_name}: {e}")
            else:
                masters = set()
                for date_entry in log_data.values():
                    if not isinstance(date_entry, dict):
                        continue
                    for filt_entry in date_entry.values():
                        if not isinstance(filt_entry, dict):
                            continue
                        for expt_entry in filt_entry.values():
                            if not isinstance(expt_entry, dict):
                                continue
                            for key in ("flat_master", "dark_master"):
                                v = expt_entry.get(key)
                                if v:
                                    masters.add(v)
                stats["calibration_frames_used"] = len(masters)

    reg_dir = Path(registered_base_dir) / target_name
    if reg_dir.exists():
        def _count_images(d: Path) -> int:
            if not d.exists():
                return 0
            return sum(
                1 for p in d.rglob("*")
                if p.is_file() and p.suffix.lower() in (".fits", ".xisf")
            )

        # run_routine.py's pipeline triages *registered* frames and moves the
        # survivors to REGISTERED/<target>/BLINKED/ (rejects to REJECTED/),
        # so those still count as registered, and BLINKED/ counts as kept.
        registered_blinked = _count_images(reg_dir / "BLINKED")
        stats["registered_frames"] = (
            _count_images(reg_dir / "registered")
            + registered_blinked
            + _count_images(reg_dir / "REJECTED")
        )
        stats["calibrated_blinked_frames"] += registered_blinked

        master_subdir = reg_dir / "master"
        if master_subdir.exists():
            stats["stacked_masters"] = sum(
                1 for p in master_subdir.glob("masterLight*") if p.is_file()
            )

    if stats["total_lights"] > 0:
        stats["true_yield_pct"] = round(
            100.0 * stats["calibrated_blinked_frames"] / stats["total_lights"], 1
        )

    return stats


## ============== Database ============== ##

class SkyStackDatabase:
    """
    SQLite-backed status tracking for SkyStack targets.

    Example:
        >>> with SkyStackDatabase(Path("skystack.db")) as db:
        ...     db.refresh_all_targets(light_base_dir, calibrated_dir, masters_dir)
        ...     for t in db.get_all_targets():
        ...         print(t)
    """

    def __init__(self, db_path: Path = Path("skystack.db")):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.initialize_database()
        logger.info(f"Database initialized at {db_path}")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
            logger.error(f"Database error: {exc_val}")
        else:
            self.conn.commit()
        self.close()

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def initialize_database(self):
        """Idempotent: creates tables/indices only if they don't exist."""
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                total_lights INTEGER DEFAULT 0,
                nights INTEGER DEFAULT 0,
                blinked_lights INTEGER DEFAULT 0,
                rejected_lights INTEGER DEFAULT 0,
                unreviewed_lights INTEGER DEFAULT 0,
                blink_yield_pct REAL,
                calibration_frames_used INTEGER DEFAULT 0,
                calibrated_frames INTEGER DEFAULT 0,
                calibrated_blinked_frames INTEGER DEFAULT 0,
                true_yield_pct REAL,
                registered_frames INTEGER DEFAULT 0,
                stacked_masters INTEGER DEFAULT 0,
                last_scanned TIMESTAMP,
                last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS light_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                local_file_path TEXT UNIQUE NOT NULL,
                remote_file_path TEXT,
                filter_name TEXT,
                exposure_time REAL,
                gain INTEGER,
                offset INTEGER,
                temperature REAL,
                rotation REAL,
                date_obs TIMESTAMP,
                obs_night TEXT,
                status TEXT DEFAULT 'acquired',
                quality_score REAL,
                calibration_log TEXT,
                master_flat_id INTEGER,
                master_dark_id INTEGER,
                header_data TEXT DEFAULT '{}',
                file_hash TEXT,
                FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE,
                FOREIGN KEY (master_flat_id) REFERENCES master_calibrations(id),
                FOREIGN KEY (master_dark_id) REFERENCES master_calibrations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calibration_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_type TEXT NOT NULL CHECK(frame_type IN ('FLAT', 'DARK', 'BIAS')),
                local_file_path TEXT UNIQUE NOT NULL,
                remote_file_path TEXT,
                filter_name TEXT,
                exposure_time REAL,
                gain INTEGER,
                offset INTEGER,
                temperature REAL,
                rotation REAL,
                date_obs TIMESTAMP,
                obs_night TEXT,
                status TEXT DEFAULT 'acquired'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS master_calibrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_type TEXT NOT NULL CHECK(frame_type IN ('FLAT', 'DARK', 'BIAS')),
                file_path TEXT UNIQUE NOT NULL,
                filter_name TEXT,
                exposure_time REAL,
                gain INTEGER NOT NULL,
                offset INTEGER NOT NULL,
                temperature REAL,
                rotation REAL,
                creation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                num_subs INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                process_type TEXT NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status TEXT DEFAULT 'running',
                log_data TEXT,
                output_files TEXT,
                error_message TEXT,
                FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
            )
        """)

        from skystack.frame_metrics import METRICS
        metric_cols = "".join(f"                {m} REAL,\n" for m in METRICS)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS frame_metrics (
                target TEXT NOT NULL,
                raw_stem TEXT NOT NULL,
                file_path TEXT NOT NULL,
                filter_name TEXT,
                exposure_time REAL,
                date_obs TEXT,
{metric_cols}                measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (target, raw_stem)
            )
        """)

        # Lightweight migration for databases created before a column was
        # added -- CREATE TABLE IF NOT EXISTS above won't alter an existing
        # table, so patch it in place instead of requiring a manual rebuild.
        for statement in (
            "ALTER TABLE targets ADD COLUMN rejected_lights INTEGER DEFAULT 0",
            "ALTER TABLE targets ADD COLUMN calibrated_blinked_frames INTEGER DEFAULT 0",
            "ALTER TABLE targets ADD COLUMN true_yield_pct REAL",
            "ALTER TABLE master_calibrations ADD COLUMN obs_date TIMESTAMP",
            "ALTER TABLE master_calibrations ADD COLUMN file_mtime REAL",
        ):
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_light_frames_target ON light_frames(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_light_frames_status ON light_frames(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calibration_frames_type ON calibration_frames(frame_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_master_type ON master_calibrations(frame_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_target ON processing_logs(target_id)")

        self.conn.commit()
        self.close()
        logger.info("Database schema initialized successfully")

    # ============== Target Operations ==============

    def add_target(self, target: TargetInfo) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO targets (name, notes, created_date) VALUES (?, ?, ?)",
            (target.name, target.notes, datetime.now().isoformat()),
        )
        self.conn.commit()
        logger.info(f"Added new target: {target.name} (ID: {cursor.lastrowid})")
        return cursor.lastrowid

    def get_target(self, target_id: int) -> Optional[TargetInfo]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM targets WHERE id = ?", (target_id,))
        row = cursor.fetchone()
        return TargetInfo(**dict(row)) if row else None

    def get_target_by_name(self, name: str) -> Optional[TargetInfo]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM targets WHERE LOWER(name) = LOWER(?)", (name,))
        row = cursor.fetchone()
        return TargetInfo(**dict(row)) if row else None

    def get_all_targets(self) -> List[TargetInfo]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM targets ORDER BY name ASC")
        return [TargetInfo(**dict(row)) for row in cursor.fetchall()]

    def upsert_target_stats(self, target_name: str, stats: Dict[str, Any]) -> int:
        """Insert or update a target's aggregate stats, as produced by
        compute_target_stats()."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM targets WHERE name = ?", (target_name,))
        row = cursor.fetchone()
        now = datetime.now().isoformat()

        if row is None:
            cursor.execute(
                "INSERT INTO targets (name, created_date) VALUES (?, ?)",
                (target_name, now),
            )
            target_id = cursor.lastrowid
        else:
            target_id = row["id"]

        cursor.execute(
            """
            UPDATE targets SET
                total_lights = ?, nights = ?, blinked_lights = ?, rejected_lights = ?,
                unreviewed_lights = ?, blink_yield_pct = ?,
                calibration_frames_used = ?, calibrated_frames = ?,
                calibrated_blinked_frames = ?, true_yield_pct = ?,
                registered_frames = ?, stacked_masters = ?,
                last_scanned = ?, last_modified = ?
            WHERE id = ?
            """,
            (
                stats["total_lights"], stats["nights"], stats["blinked_lights"],
                stats["rejected_lights"], stats["unreviewed_lights"], stats["blink_yield_pct"],
                stats["calibration_frames_used"], stats["calibrated_frames"],
                stats["calibrated_blinked_frames"], stats["true_yield_pct"],
                stats["registered_frames"], stats["stacked_masters"],
                now, now, target_id,
            ),
        )
        self.conn.commit()
        return target_id

    def refresh_all_targets(
        self,
        light_base_dir: Path,
        calibrated_base_dir: Path,
        registered_base_dir: Path,
    ) -> int:
        """Scan every target directory under light_base_dir and upsert its
        stats. Returns the number of targets refreshed."""
        light_base_dir = Path(light_base_dir)
        if not light_base_dir.exists():
            logger.warning(f"Light base dir does not exist: {light_base_dir}")
            return 0

        count = 0
        for entry in sorted(light_base_dir.iterdir()):
            if not entry.is_dir():
                continue
            stats = compute_target_stats(
                entry.name, light_base_dir, calibrated_base_dir, registered_base_dir
            )
            self.upsert_target_stats(entry.name, stats)
            count += 1

        logger.info(f"Refreshed stats for {count} targets")
        return count

    # ============== Light Frame Operations ==============

    def add_light_frame(self, frame: LightFrame) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO light_frames (
                target_id, local_file_path, remote_file_path, filter_name,
                exposure_time, gain, offset, temperature, rotation, date_obs,
                obs_night, status, quality_score, header_data, file_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                frame.target_id, frame.local_file_path, frame.remote_file_path,
                frame.filter_name, frame.exposure_time, frame.gain, frame.offset,
                frame.temperature, frame.rotation, frame.date_obs, frame.obs_night,
                frame.status, frame.quality_score, frame.header_data, frame.file_hash,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_light_frames(
        self, target_id: int, status: Optional[str] = None
    ) -> List[LightFrame]:
        query = "SELECT * FROM light_frames WHERE target_id = ?"
        params: List[Any] = [target_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY date_obs DESC"

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return [LightFrame(**dict(row)) for row in cursor.fetchall()]

    # ============== Calibration Frame Operations ==============

    def add_calibration_frame(self, calib: CalibrationFrame) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO calibration_frames (
                frame_type, local_file_path, remote_file_path, filter_name,
                exposure_time, gain, offset, temperature, rotation,
                date_obs, obs_night
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                calib.frame_type, calib.local_file_path, calib.remote_file_path,
                calib.filter_name, calib.exposure_time, calib.gain, calib.offset,
                calib.temperature, calib.rotation, calib.date_obs, calib.obs_night,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_calibration_frames(
        self, frame_type: str, filter_name: Optional[str] = None
    ) -> List[CalibrationFrame]:
        query = "SELECT * FROM calibration_frames WHERE frame_type = ?"
        params: List[Any] = [frame_type]
        if filter_name:
            query += " AND filter_name = ?"
            params.append(filter_name)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return [CalibrationFrame(**dict(row)) for row in cursor.fetchall()]

    # ============== Master Calibration Operations ==============

    def add_master_calibration(self, master: MasterCalibration) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO master_calibrations (
                frame_type, file_path, filter_name, exposure_time,
                gain, offset, temperature, rotation, num_subs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                master.frame_type, master.file_path, master.filter_name,
                master.exposure_time, master.gain, master.offset,
                master.temperature, master.rotation, master.num_subs,
            ),
        )
        self.conn.commit()
        logger.info(f"Added master {master.frame_type}: {master.file_path}")
        return cursor.lastrowid

    def sync_master_calibrations(self, master_dir: Path, frame_type: str) -> Dict[str, int]:
        """Bring the master_calibrations rows for master_dir in line with the
        files on disk. Only new or modified masters have their headers read,
        so this is cheap to run before every calibration."""
        from astro_utils.fits_helpers import list_master_files, master_index_row

        master_dir = Path(master_dir)
        frame_type = frame_type.upper()
        known = {
            row["file_path"]: row["file_mtime"]
            for row in self.conn.execute(
                "SELECT file_path, file_mtime FROM master_calibrations WHERE frame_type = ?",
                (frame_type,),
            )
            if Path(row["file_path"]).parent == master_dir
        }
        on_disk = {str(p): p for p in list_master_files(master_dir)} if master_dir.exists() else {}

        counts = {"added": 0, "updated": 0, "removed": 0, "failed": 0}
        for path_str, p in on_disk.items():
            mtime = p.stat().st_mtime
            if path_str in known and known[path_str] == mtime:
                continue
            try:
                row = master_index_row(p, frame_type)
            except RuntimeError as e:
                logger.error(str(e))
                counts["failed"] += 1
                continue
            if row["gain"] is None or row["offset"] is None:
                # calibration matching requires both, so such a master can never be picked
                logger.warning(f"Skipping master without GAIN/OFFSET: {p}")
                counts["failed"] += 1
                continue
            date = row["date"]
            self.conn.execute(
                """
                INSERT INTO master_calibrations (
                    frame_type, file_path, filter_name, exposure_time, gain, offset,
                    temperature, rotation, obs_date, file_mtime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    frame_type = excluded.frame_type, filter_name = excluded.filter_name,
                    exposure_time = excluded.exposure_time, gain = excluded.gain,
                    offset = excluded.offset, temperature = excluded.temperature,
                    rotation = excluded.rotation, obs_date = excluded.obs_date,
                    file_mtime = excluded.file_mtime
                """,
                (
                    frame_type, path_str, row["filter"], row["exptime"], row["gain"],
                    row["offset"], row["temp"], row["rot"],
                    None if date is None or date != date else date.isoformat(),  # NaT != NaT
                    mtime,
                ),
            )
            counts["updated" if path_str in known else "added"] += 1

        gone = [p for p in known if p not in on_disk]
        self.conn.executemany("DELETE FROM master_calibrations WHERE file_path = ?",
                              [(p,) for p in gone])
        counts["removed"] = len(gone)
        self.conn.commit()
        logger.info(f"Master {frame_type.lower()}s in {master_dir}: {len(on_disk)} on disk "
                    f"(+{counts['added']} new, {counts['updated']} updated, "
                    f"-{counts['removed']} removed, {counts['failed']} unreadable)")
        return counts

    def master_calibrations_frame(self, frame_type: str, master_dir: Optional[Path] = None):
        """Masters as a DataFrame with the same columns as the old
        master_index.parquet, for calibrate_images.find_best_masterv2."""
        import pandas as pd
        from astro_utils.fits_helpers import MASTER_INDEX_COLUMNS

        rows = [
            {
                "path": r["file_path"],
                "fmt": Path(r["file_path"]).suffix.lower().lstrip("."),
                "type": r["frame_type"],
                "date": r["obs_date"],
                "filter": r["filter_name"],
                "gain": r["gain"],
                "offset": r["offset"],
                "exptime": r["exposure_time"],
                "temp": r["temperature"],
                "rot": r["rotation"],
            }
            for r in self.conn.execute(
                "SELECT * FROM master_calibrations WHERE frame_type = ? ORDER BY file_path",
                (frame_type.upper(),),
            )
            if master_dir is None or Path(r["file_path"]).parent == Path(master_dir)
        ]
        df = pd.DataFrame(rows, columns=MASTER_INDEX_COLUMNS)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df

    # ============== Frame quality metrics ==============
    # Keyed by (target, raw stem) so a frame's metrics follow it from
    # CALIBRATED/ through registered/ to BLINKED/ or REJECTED/.

    def upsert_frame_metrics(self, target: str, rows: List[Dict[str, Any]]) -> int:
        """rows: {"raw_stem", "file_path", "filter_name", "exposure_time",
        "date_obs", <metric>: value, ...}. Returns the number written."""
        from skystack.frame_metrics import METRICS

        cols = ["target", "raw_stem", "file_path", "filter_name", "exposure_time",
                "date_obs", *METRICS, "measured_at"]
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.executemany(
            f"INSERT OR REPLACE INTO frame_metrics ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [(target, *(r.get(c) for c in cols[1:-1]), now) for r in rows],
        )
        self.conn.commit()
        return len(rows)

    def measured_stems(self, target: str) -> set:
        return {r[0] for r in self.conn.execute(
            "SELECT raw_stem FROM frame_metrics WHERE target = ?", (target,))}

    def frame_metrics_frame(self, target: str):
        import pandas as pd

        return pd.read_sql_query(
            "SELECT * FROM frame_metrics WHERE target = ? ORDER BY date_obs, raw_stem",
            self.conn, params=(target,),
        )

    # ============== Processing Log Operations ==============

    def start_processing(self, target_id: int, process_type: ProcessType) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO processing_logs (target_id, process_type, status) VALUES (?, ?, 'running')",
            (target_id, process_type.value),
        )
        self.conn.commit()
        return cursor.lastrowid

    def complete_processing(
        self,
        log_id: int,
        status: str = "completed",
        log_data: Optional[str] = None,
        output_files: Optional[List[str]] = None,
        error_message: Optional[str] = None,
    ):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE processing_logs
            SET end_time = ?, status = ?, log_data = ?, output_files = ?, error_message = ?
            WHERE id = ?
            """,
            (
                datetime.now().isoformat(), status, log_data,
                json.dumps(output_files) if output_files else None,
                error_message, log_id,
            ),
        )
        self.conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s - %(levelname)s - %(message)s")

    from astro_utils.config_loader import load_config

    config = load_config(Path(__file__).parent / "config.yaml")
    light_base_dir = Path(config.get("light_base_dir"))
    calibrated_base_dir = Path(config.get("calibrated_output_dir"))
    registered_base_dir = Path(config.get("masters_dir"))

    with SkyStackDatabase(Path(__file__).parent / "skystack.db") as db:
        n = db.refresh_all_targets(light_base_dir, calibrated_base_dir, registered_base_dir)
        print(f"Refreshed {n} targets.")
        for t in db.get_all_targets():
            print(
                f"  {t.name}: {t.total_lights} lights, {t.nights} nights, "
                f"{t.blinked_lights} blinked ({t.blink_yield_pct}%), "
                f"{t.calibrated_frames} calibrated, {t.registered_frames} registered, "
                f"{t.stacked_masters} stacked"
            )
