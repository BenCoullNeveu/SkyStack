import gc
import pandas as pd
from os import path
from pathlib import Path
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from xisf import XISF
from astropy.io import fits
from astropy.time import Time
import logging
import yaml
import time

logger = logging.getLogger(__name__)

# LOAD CONFIG FROM PARENT
config_path = Path(__file__).parent.parent / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)
    
SUPPORTED_FORMATS = ("fits", "xisf", "auto")

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



def detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".fits", ".fit", ".fts"):
        return "fits"
    if ext == ".xisf":
        return "xisf"
    # fallback
    return "fits"


def list_images(base_dir: Path, fmt: str = "fits"):
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"fmt must be one of {SUPPORTED_FORMATS}, got {fmt}")

    if fmt == "fits":
        exts = (".fits", ".fit", ".fts")
    elif fmt == "xisf":
        exts = (".xisf",)
    else:  # auto
        exts = (".fits", ".fit", ".fts", ".xisf")

    return [p for p in base_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts]


def to_native_endian(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr)
    # FITS often yields big-endian (>f8, >i2, etc.) which breaks XISF._get_sampleFormat lookup
    if arr.dtype.byteorder == ">":
        arr = arr.byteswap().view(arr.dtype.newbyteorder("="))
    return np.ascontiguousarray(arr)


def safe_unlink(path: Path, attempts: int = 10, delay_s: float = 0.15):
    """
    Windows-friendly delete: retries briefly for transient locks (Defender, indexer, Explorer, etc.).
    """
    last_err = None
    for _ in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay_s)
    raise last_err



## == Reading metadata == ##

def _read_fits_header(path: Path) -> fits.Header:
    try:
        # memmap=False prevents Win file locks
        with fits.open(path, memmap=False) as hdul:
            return hdul[0].header.copy()
    except Exception:
        logger.exception("Failed to read FITS header: %s", path)
        return fits.Header()

def _read_xisf_image_metadata(path: Path) -> dict:
    """
    Returns the image metadata dict from the first image block.
    """
    try:
        x = XISF(str(path))
        ims = x.get_images_metadata()
        if not ims:
            return {}
        return ims[0]
    except Exception:
        logger.exception("Failed to read XISF metadata: %s", path)
        return {}


def _xisf_keywords_to_header_dict(img_meta: dict) -> dict:
    """
    Flatten XISF FITSKeywords dict-of-lists into a simple dict: {KEY: value}.
    """
    out = {}
    fk = (img_meta or {}).get("FITSKeywords", {}) or {}
    for key, entries in fk.items():
        if not entries:
            continue
        # Keep last occurrence
        out[key] = entries[-1].get("value")
    return out

def read_header_info(path: Path, fmt: str = "auto") -> dict:
    """
    Return a simple key->value mapping of header-ish metadata for FITS or XISF.
    For XISF, this reads the embedded FITSKeywords (not PixInsight properties).
    """
    if fmt == "auto":
        fmt = detect_format(path)

    fmt = fmt.lower()
    if fmt == "fits":
        hdr = _read_fits_header(path)
        return {k: hdr.get(k) for k in hdr.keys()}
    elif fmt == "xisf":
        img_meta = _read_xisf_image_metadata(path)
        return _xisf_keywords_to_header_dict(img_meta)
    else:
        raise ValueError(f"Unsupported fmt={fmt}")
    
def _ensure_fitskeywords_dict(img_meta: dict) -> dict:
    img_meta = img_meta or {}
    fk = img_meta.get("FITSKeywords")
    if not isinstance(fk, dict):
        fk = {}
    img_meta["FITSKeywords"] = fk
    return img_meta

def write_header_info(path: Path, header_info: dict, fmt: str = "auto"):
    """
    Update FITS header (in-place) or XISF FITSKeywords (rewrite file).
    header_info: {KEY: value} where value is scalar or None.
    """
    try:
        if fmt == "auto":
            fmt = detect_format(path)
        fmt = fmt.lower()

        if fmt == "fits":
            with fits.open(path, mode="update", memmap=False) as hdul:
                hdr = hdul[0].header
                for key, value in header_info.items():
                    if value is None:
                        hdr[key] = fits.card.UNDEFINED
                    else:
                        hdr[key] = value
                hdul.flush()
            return

        if fmt == "xisf":
            # Read existing image + metadata
            file_meta = {}
            img_meta = {}
            im_data = XISF.read(str(path), n=0, image_metadata=img_meta, xisf_metadata=file_meta)

            # Ensure we have FITSKeywords
            img_meta = _ensure_fitskeywords_dict(img_meta)

            # Update FITSKeywords; keep comment if present, else blank
            for key, value in header_info.items():
                if value is None:
                    # represent undefined as empty string to avoid weird "UNDEFINED" strings
                    new_val = ""
                else:
                    new_val = str(value)

                existing = img_meta["FITSKeywords"].get(key, [])
                comment = existing[-1].get("comment", "") if existing else ""
                img_meta["FITSKeywords"][key] = [{"value": new_val, "comment": comment}]

            # Rewrite file
            XISF.write(
                str(path),
                to_native_endian(np.array(im_data, copy=True)),
                creator_app=file_meta.get("XISF:CreatorApplication", {}).get("value", "SkyStack"),
                image_metadata=img_meta,
                xisf_metadata=file_meta,
                codec=None,      # default to None, but can detect from img_meta.get("compression")
                shuffle=True,
            )
            return

        raise ValueError(f"Unsupported fmt={fmt}")

    except Exception as e:
        logger.error(f"Failed to write header info to {path}: {e}")

# def write_header_info(path: Path, header_info: dict, fmt="fits"):
#     """Write metadata to FITS header."""
#     try:
#         if fmt.lower() == "fits":
#             with fits.open(path, mode='update') as hdul:
#                 hdr = hdul[0].header
#                 for key, value in header_info.items():
#                     if value is not None:
#                         hdr[key] = value
#                     else:
#                         hdr[key] = fits.card.UNDEFINED
#                 hdul.flush()
#         elif fmt.lower() == "xisf":
#             xisf = XISF(path)
#             metadata = xisf.get_file_metadata()
#             for key, value in header_info.items():
#                 if value is not None:
#                     metadata[key] = value
#                 else:
#                     metadata[key] = "UNDEFINED"
#             xisf.write(path, xisf.data, xisf.creator, metadata, codec=xisf.codec, shuffle=True)
#     except Exception as e:
#         logger.error(f"Failed to write header info to {path}: {e}")


def get_val(key:str, tup:tuple)->any:
    """Get value from tuple of (key, value) pairs by key."""
    for k, v in tup:
        if k == key:
            return v
    raise KeyError(f"Key {key} not found in tuple. Available keys: {[k for k,v in tup]}")

def read_image_header_info(path: Path, fmt: str = "auto") -> dict:
    """Extract relevant metadata from image header."""
    hdr = read_header_info(path, fmt=fmt)

    return {
        HEADER_FILTER_KEY: hdr.get(HEADER_FILTER_KEY),
        HEADER_ROTATION_KEY: _safe_cast_float(hdr.get(HEADER_ROTATION_KEY)),
        HEADER_GAIN_KEY: _safe_cast_int(hdr.get(HEADER_GAIN_KEY)),
        HEADER_OFFSET_KEY: _safe_cast_int(hdr.get(HEADER_OFFSET_KEY)),
        HEADER_EXPTIME_KEY: _safe_cast_float(hdr.get(HEADER_EXPTIME_KEY, 0.0)),
        HEADER_TEMPERATURE_KEY: _safe_cast_float(hdr.get(HEADER_TEMPERATURE_KEY)),
        "DATE-OBS": hdr.get("DATE-OBS"),
        "DATE-LOC": hdr.get("DATE-LOC"),
    }

def _safe_cast_float(v):
    try:
        return None if v is None else float(v)
    except Exception:
        return None
    
def _safe_cast_int(v):
    try:
        return None if v is None else int(v)
    except Exception:
        return None

def parse_date_from_header(path: Path, fmt: str = "auto"):
    """
    Extract observing date from DATE-LOC or DATE-OBS.
    Returns datetime.date or None.
    """
    hdr = read_image_header_info(path, fmt=fmt)
    date_str = hdr.get("DATE-LOC") or hdr.get("DATE-OBS")
    if not date_str:
        return None

    # Common FITS format: 2025-10-08T23:14:02
    for fmt_str in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(date_str, fmt_str).date()
        except Exception:
            continue

    logger.error("Failed to parse date string %r in %s", date_str, path)
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
        return datetime.strptime(master_name.split("_")[-1].replace(".fits", "").replace(".xisf", ""), "%d%m%Y").date()
    except Exception as e:
        logger.error(f"Failed to parse date from master name {master_name}: {e}")
        return None

## == Grouping images by metadata == ##
def group_images_by_metadata(
    base_dir: Path,
    days_within: int = 0,
    ignore_rotation: bool = False,
    fmt: str = "fits",  # "fits", "xisf", or "auto"
):
    """
    Group images (FITS/XISF) by matched metadata.
    """
    files = list_images(base_dir, fmt=fmt)

    entries = []
    for p in files:
        info = read_image_header_info(p, fmt="auto")  # per-file detect
        date = parse_date_from_header(p, fmt="auto") or parse_date_from_path(p) or None

        entries.append({
            "path": p,
            "filter": info.get(HEADER_FILTER_KEY),
            "rot": info.get(HEADER_ROTATION_KEY),
            "gain": info.get(HEADER_GAIN_KEY),
            "offset": info.get(HEADER_OFFSET_KEY),
            "date": date,
            "exptime": info.get(HEADER_EXPTIME_KEY),
            "temp": info.get(HEADER_TEMPERATURE_KEY)
        })

    clusters = defaultdict(list)
    for e in entries:
        key_parts = [str(e["filter"]), str(e["gain"]), str(e["offset"])]
        if not ignore_rotation:
            key_parts.append(str(round(e["rot"], 3)) if e["rot"] is not None else "None")
        base_key = "|".join(key_parts)
        clusters[base_key].append(e)

    groups = {}
    for base_key, members in clusters.items():
        if not members:
            continue

        if days_within == 0:
            by_date = defaultdict(list)
            for m in members:
                by_date[str(m.get("date"))].append(m["path"])
            for dt, paths in by_date.items():
                if paths:
                    groups[f"{base_key}__date_{dt}"] = sorted(paths)
        else:
            def _date_or_epoch(m):
                return m.get("date") or datetime(1970, 1, 1).date()

            members_sorted = sorted(members, key=_date_or_epoch)
            current_group = [members_sorted[0]]

            for cur in members_sorted[1:]:
                prev = current_group[-1]
                prev_date = _date_or_epoch(prev)
                cur_date = _date_or_epoch(cur)

                if abs((cur_date - prev_date).days) <= days_within:
                    current_group.append(cur)
                else:
                    groups[_make_group_key(base_key, current_group)] = [m["path"] for m in current_group]
                    current_group = [cur]

            if current_group:
                groups[_make_group_key(base_key, current_group)] = [m["path"] for m in current_group]

    return groups



## == create index parquet file == ##
def build_master_index_parquet(
    master_dir: Path,
    out_parquet: Path,
    type:str,
    verbose: bool = False
):
    """
    Build a Parquet index of master calibration frames using headers only.
    This is meant to be run rarely (or cached).
    """

    rows = []

    if verbose:
        print(f"Building master index from {master_dir}...")
        nfiles = sum(1 for p in master_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".fits", ".xisf"))
        print(f"Found {nfiles} files to process.")
        i = 0
    for p in master_dir.glob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".fits", ".xisf"):
            continue
        
        if verbose:
            i += 1 
            print(f"Processing file {i}/{nfiles}: {p}" + " " * 20, end="\r")

        is_xisf = p.suffix.lower() == ".xisf"

        try:
            hdr = read_image_header_info(p, fmt="xisf" if is_xisf else "fits")
        except Exception as e:
            raise RuntimeError(f"Failed to read header for {p}, skipping. Error: {e}")
            continue
        
        dateloc = datetime.strptime(hdr.get("DATE-LOC"), "%Y-%m-%dT%H:%M:%S.%f").date() if hdr.get("DATE-LOC") else None
        dateobs = datetime.strptime(hdr.get("DATE-OBS"), "%Y-%m-%dT%H:%M:%S.%f").date() if hdr.get("DATE-OBS") else None
        
        rows.append({
            "path": str(p),
            "fmt": p.suffix.lower().lstrip("."),
            "type": type or str(hdr.get("IMAGETYP", "")).lower(),   # "master flat", "master dark", etc.
            "date": pd.to_datetime(
                                    dateloc or dateobs or parse_date_from_master_path(p),
                                    errors="coerce"
                                ),
            "filter": hdr.get("FILTER"),
            "gain": _safe_cast_int(hdr.get("GAIN")),
            "offset": _safe_cast_int(hdr.get("OFFSET")),
            "exptime": _safe_cast_float(hdr.get("EXPTIME")),
            "temp": _safe_cast_float(hdr.get("SET-TEMP")),
            "rot": _safe_cast_float(hdr.get("ROTATANG")),
        })

    df = pd.DataFrame(rows)

    # enforce stable dtypes
    # df["gain"] = df["gain"].astype("Int32")
    # df["offset"] = df["offset"].astype("Int32")
    # df["exptime"] = df["exptime"].astype("Float32")
    # df["temp"] = df["temp"].astype("Float32")
    # df["rot"] = df["rot"].astype("Float32")
    # df["filter"] = df["filter"].astype("string").str.upper()
    # df["type"] = df["type"].astype("string")

    if verbose:
        print(f"Writing master index to {out_parquet}...")
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_parquet, index=False)
    if verbose:
        print("Master index written.")
    return df



def _make_group_key(base_key: str, members: list):
    dates = sorted(set(str(m.get("date")) for m in members))
    date_part = dates[0] if len(dates) == 1 else f"{dates[0]}_to_{dates[-1]}"
    return f"{base_key}__{date_part}"


def is_flat_frame_compatible(light_path: Path, flat_path: Path, rotation_tolerance: float = ROTATION_TOLERANCE):
    light_dict = read_image_header_info(light_path, fmt="auto")
    flat_dict = read_image_header_info(flat_path, fmt="auto")
    
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


def fits2xisf(
    fits_file: Path,
    xisf_file: Path,
    delete_after_convert: bool = False,
    compression: str | None = None,
    force_float32: bool = True,
):
    """
    Convert FITS -> XISF using sergio-dr/xisf.

    compression: "zstd", "zlib", "lz4", "lz4hc", or None
    force_float32: recommended for PixInsight masters and to avoid dtype/endianness issues
    """
    fits_file = Path(fits_file)
    xisf_file = Path(xisf_file)
    xisf_file.parent.mkdir(parents=True, exist_ok=True)

    # Read FITS with no memmap (avoids Windows locks)
    with fits.open(fits_file, memmap=False) as hdul:
        img_data = None
        fits_header = None
        for hdu in hdul:
            if isinstance(hdu.data, np.ndarray):
                img_data = hdu.data
                fits_header = hdu.header
                break

    if img_data is None or fits_header is None:
        raise ValueError(f"No image HDU found in {fits_file}")

    # Copy & normalize
    img_data = np.array(img_data, copy=True)
    img_data = to_native_endian(img_data)
    if force_float32 and img_data.dtype.kind == "f":
        img_data = img_data.astype(np.float32, copy=False)

    # Ensure channel axis exists (XISF.write expects 2D w/ channels or channels-first)
    img_data = np.atleast_3d(img_data)

    # Build FITSKeywords structure (dict-of-lists)
    fits_keyw = {}
    for k in fits_header.keys():
        if not k:
            continue
        v = fits_header.get(k)
        c = fits_header.comments[k] if k in fits_header else ""
        fits_keyw.setdefault(k, []).append({"value": str(v), "comment": str(c)})

    img_meta = {"FITSKeywords": fits_keyw}

    XISF.write(
        str(xisf_file),
        img_data,
        creator_app="SkyStack_fits2xisf",
        image_metadata=img_meta,
        xisf_metadata={},
        codec=compression,
        shuffle=True,
    )

    # encourage releasing memory before delete
    del img_data
    gc.collect()

    if delete_after_convert:
        safe_unlink(fits_file)
    
    
def xisf2fits(
    xisf_file: Path,
    fits_file: Path,
    delete_after_convert: bool = False,
):
    """
    Convert XISF -> FITS using sergio-dr/xisf.
    Writes FITSKeywords back into FITS header (best-effort).
    Convert all XISF image blocks to a multi-HDU FITS file.
    Preserves dtype and attempts to restore FITS keywords.
    """
    xisf_file = Path(xisf_file)
    fits_file = Path(fits_file)
    fits_file.parent.mkdir(parents=True, exist_ok=True)

    file_meta = {}
    img_meta = {}

    data = XISF.read(
        str(xisf_file),
        n=0,
        image_metadata=img_meta,
        xisf_metadata=file_meta,
    )

    data = np.array(data, copy=True)
    data = np.squeeze(data)  # remove singleton axes
    data = to_native_endian(data)

    # Handle image dimensionality
    if data.ndim == 2:
        # Standard monochrome image
        pass

    elif data.ndim == 3:
        # Likely RGB / multi-channel image stored as (H,W,C)
        if data.shape[-1] in (3, 4):
            data = np.moveaxis(data[..., :3], -1, 0)

        # Unexpected cube/maps: keep first plane only
        else:
            data = data[0]

    else:
        raise ValueError(
            f"Unsupported image dimensions for {xisf_file.name}: {data.shape}"
        )

    print(f"Writing FITS image: {data.shape} | {data.dtype}")

    hdr = fits.Header()

    fk = img_meta.get("FITSKeywords") or {}
    for key, entries in fk.items():
        if not entries:
            continue

        last = entries[-1]
        val = last.get("value", "")
        com = last.get("comment", "")

        try:
            hdr[key] = (val, com)
        except Exception:
            # Ignore malformed FITS cards
            pass

    fits.PrimaryHDU(data=data, header=hdr).writeto(
        fits_file,
        overwrite=True,
        output_verify="silentfix",
    )

    del data
    gc.collect()

    if delete_after_convert:
        safe_unlink(xisf_file)