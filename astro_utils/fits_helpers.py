from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from astropy.io import fits
from astropy.time import Time
import logging
import yaml

logger = logging.getLogger(__name__)

# LOAD CONFIG FROM PARENT
config_path = Path(__file__).parent.parent / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)

# Change these to match your FITS headers
HEADER_FILTER_KEY = config.get("header_filter_key", "FILTER")
HEADER_ROTATION_KEY = config.get("header_rotation_key", "ROTATANG")
HEADER_GAIN_KEY = config.get("header_gain_key", "GAIN")
HEADER_OFFSET_KEY = config.get("header_offset_key", "OFFSET")
HEADER_EXPTIME_KEY = config.get("header_exptime_key", "EXPTIME")
HEADER_TEMPERATURE_KEY = config.get("header_temperature_key", "SET-TEMP")

# Tolerances
EXPOSURE_TOLERANCE = config.get("exposure_tolerance", 1)
ROTATION_TOLERANCE = config.get("rotation_tolerance", 0.5)
TEMP_TOLERANCE = config.get("temp_tolerance", 1.0)

def write_header_info(path: Path, header_info: dict):
    """Write metadata to FITS header."""
    try:
        with fits.open(path, mode='update') as hdul:
            hdr = hdul[0].header
            for key, value in header_info.items():
                if value is not None:
                    hdr[key] = value
                else:
                    hdr[key] = fits.card.UNDEFINED
            hdul.flush()
    except Exception as e:
        logger.error(f"Failed to write header info to {path}: {e}")

def _read_header(path: Path):
    try:
        with fits.open(path) as hdul:
            hdr = hdul[0].header
            return hdr
    except Exception as e:
        logger.exception("Failed to read FITS header: %s", path)
        return {}

def get_val(key:str, tup:tuple)->any:
    """Get value from tuple of (key, value) pairs by key."""
    for k, v in tup:
        if k == key:
            return v
    raise KeyError(f"Key {key} not found in tuple. Available keys: {[k for k,v in tup]}")

def read_fits_header_info(path: Path):
    """Extract relevant metadata from FITS header.
    Returns a dictionary of metadata values.
    """
    hdr = _read_header(path)
    fit_dict = {
        HEADER_FILTER_KEY: hdr.get(HEADER_FILTER_KEY),
        HEADER_ROTATION_KEY: _safe_cast_float(hdr.get(HEADER_ROTATION_KEY)),
        HEADER_GAIN_KEY: hdr.get(HEADER_GAIN_KEY),
        HEADER_OFFSET_KEY: hdr.get(HEADER_OFFSET_KEY),
        HEADER_EXPTIME_KEY: _safe_cast_float(hdr.get(HEADER_EXPTIME_KEY, 0.0)),
        HEADER_TEMPERATURE_KEY: _safe_cast_float(hdr.get(HEADER_TEMPERATURE_KEY))
    }
    return fit_dict

def _safe_cast_float(v):
    try:
        return None if v is None else float(v)
    except Exception:
        return None

def parse_date_from_header(path: Path):
    """Extract date from FITS header.
    Tries several header keywords to find the date-12hours. 
    This will be useful when grouping by imaging night.
    -> If not DATE-LOC, then DATE-OBS. 
    """
    date_keys = ("DATE-LOC", "DATE-OBS")
    try:
        hdr = read_fits_header_info(path)
    except:
        logger.error(f"Failed to read FITS header: {path}")
    date_str = hdr.get("DATE-OBS")
    if date_str:
        try:
            return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").date()
        except Exception as e:
            logger.error(f"Failed to parse DATE-OBS from header: {e}")
    return None

# SUPERSEDED BY parse_date_from_header. Will soon be deprecated.
def parse_date_from_path(path: Path):
    """Try to extract a date from the path parts with YYYY-MM-DD format.

    Returns datetime.date or None.
    """
    for part in reversed(path.parts):
        try:
            return datetime.strptime(part.split("_")[0], "%Y-%m-%d").date()
        except Exception:
            continue
    return None

# SUPERSEDED BY parse_date_from_header. Will soon be deprecated.
def parse_date_from_master_path(master_path: Path):
    """Extract date from master file path in format DDMMYYYY."""
    master_name = master_path.name
    try:
        return datetime.strptime(master_name.split("_")[-1].replace(".fits", ""), "%d%m%Y").date()
    except Exception as e:
        logger.error(f"Failed to parse date from master name {master_name}: {e}")
        return None


def group_fits_by_metadata(base_dir: Path, days_within: int = 0, ignore_rotation: bool = False):
    """Group FITS files by matched metadata.

    - days_within: maximum allowed difference in days between files in same group. 0 means strict same-day.
    - ignore_rotation: when True, rotation is not part of the grouping key.

    Returns dict: {group_key: [Path,...]}
    group_key is a readable string.
    """
    files = [p for p in base_dir.rglob("*.fits") if p.is_file()]

    # Build entries with metadata
    entries = []
    for p in files:
        fits_dict = read_fits_header_info(p)
        date = parse_date_from_path(p) or None
        entries.append({
            "path": p,
            "filter": fits_dict.get(HEADER_FILTER_KEY),
            "rot": fits_dict.get(HEADER_ROTATION_KEY),
            "gain": fits_dict.get(HEADER_GAIN_KEY),
            "offset": fits_dict.get(HEADER_OFFSET_KEY),
            "date": date,
            "exptime": fits_dict.get(HEADER_EXPTIME_KEY),
            "temp": fits_dict.get(HEADER_TEMPERATURE_KEY)
        })

    # Naive grouping: cluster by exact equality of non-date metadata, optionally rotation,
    # then split clusters by date windows.
    clusters = defaultdict(list)
    for e in entries:
        key_parts = [str(e["filter"]), str(e["gain"]), str(e["offset"])]
        if not ignore_rotation:
            key_parts.append(str(round(e["rot"], 3)) if e["rot"] is not None else "None")
        base_key = "|".join(key_parts)
        clusters[base_key].append(e)

    groups = {}
    for base_key, members in clusters.items():
        # if days_within == 0 then treat per-date (must be same date)
        if days_within == 0:
            by_date = defaultdict(list)
            for m in members:
                by_date[str(m.get("date"))].append(m["path"])
            for dt, paths in by_date.items():
                if not paths:
                    continue
                key = f"{base_key}__date_{dt}"
                groups[key] = sorted(paths)
        else:
            # sort by date (if missing date put at epoch)
            def _date_or_epoch(m):
                return m.get("date") or datetime(1970, 1, 1).date()

            members_sorted = sorted(members, key=_date_or_epoch)
            # sliding window grouping
            current_group = [members_sorted[0]]
            for cur in members_sorted[1:]:
                prev = current_group[-1]
                prev_date = prev.get("date") or datetime(1970, 1, 1).date()
                cur_date = cur.get("date") or datetime(1970, 1, 1).date()
                delta_days = abs((cur_date - prev_date).days)
                if delta_days <= days_within:
                    current_group.append(cur)
                else:
                    # flush
                    key = _make_group_key(base_key, current_group)
                    groups[key] = [m["path"] for m in current_group]
                    current_group = [cur]
            # flush last
            if current_group:
                key = _make_group_key(base_key, current_group)
                groups[key] = [m["path"] for m in current_group]

    return groups


def _make_group_key(base_key: str, members: list):
    dates = sorted(set(str(m.get("date")) for m in members))
    date_part = dates[0] if len(dates) == 1 else f"{dates[0]}_to_{dates[-1]}"
    return f"{base_key}__{date_part}"


def is_flat_frame_compatible(light_path: Path, flat_path: Path, rotation_tolerance: float = ROTATION_TOLERANCE):
    light_dict = read_fits_header_info(light_path)
    flat_dict = read_fits_header_info(flat_path)
    
    # collect metadata
    lfilt = light_dict.get(HEADER_FILTER_KEY)
    ffilt = flat_dict.get(HEADER_FILTER_KEY)
    lgain = light_dict.get(HEADER_GAIN_KEY)
    fgain = flat_dict.get(HEADER_GAIN_KEY)
    loffset = light_dict.get(HEADER_OFFSET_KEY)
    foffset = flat_dict.get(HEADER_OFFSET_KEY)
    lrot = light_dict.get(HEADER_ROTATION_KEY)
    frot = flat_dict.get(HEADER_ROTATION_KEY)
    
    # return early if any metadata is missing
    if lfilt is None or ffilt is None:
        return False, "Missing filter information in one of the frames"
    if lgain is None or fgain is None:
        return False, "Missing gain information in one of the frames"
    if loffset is None or foffset is None:
        return False, "Missing offset information in one of the frames"

    # Basic checks
    if lfilt != ffilt:
        return False, f"Filter mismatch (light={lfilt} flat={ffilt})"
    if lgain != fgain:
        return False, f"Gain mismatch (light={lgain} flat={fgain})"
    if loffset != foffset:
        return False, f"Offset mismatch (light={loffset} flat={foffset})"
    if lrot is None or frot is None:
        return False, "Rotation info missing"
    if abs(lrot - frot) > rotation_tolerance:
        return False, f"Rotation off by {abs(lrot-frot):.2f}° (tolerance {rotation_tolerance}°)"
    return True, ""