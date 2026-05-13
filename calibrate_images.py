#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
from collections import defaultdict
from time import sleep, time
from astro_utils.config_loader import load_config
from astro_utils.fits_helpers import read_image_header_info, parse_date_from_path, parse_date_from_master_path, detect_format

from astro_utils.pixinsight_cli import launch_pixinsight, close_pixinsight, calibrate_with_pixinsight
from astro_utils.file_ops import ensure_dir, get_target_name, file_exists_in_tree
from astro_utils.plotting import plot_summary
import logging
import json

# --- Logging ---
logger = logging.getLogger(__name__)

# --- Load config ---
config = load_config(Path(__file__).parent / "config.yaml")

TARGET_NAME = config.get("target_name", None)
LIGHT_BASE_DIR = Path(config.get("light_base_dir", "D:/Astrophotography/SkyShare Data/LIGHT"))
CALIBRATED_OUTPUT_DIR = Path(config.get("calibrated_output_dir", "D:/Astrophotography/SkyShare Data/CALIBRATED"))
FLATS_DIR = Path(config.get("flat_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/FLAT"))
DARKS_DIR = Path(config.get("dark_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/DARK"))

MAX_FLAT_DAYS_DIFF = config.get("calibration_search_days_flat", 4)
MAX_DARK_MONTHS_DIFF = config.get("calibration_search_months_dark", 3)
ROT_TOLERANCE = config.get("flat_rotation_tolerance", 0.5)
OVERWRITE_EXISTING = config.get("overwrite_existing", False)
ONLY_PLOT = config.get("only_plot", False)

# FITS keywords
HEADER_FILTER_KEY = config.get("header_filter_key")
HEADER_ROTATION_KEY = config.get("header_rotation_key")
HEADER_GAIN_KEY = config.get("header_gain_key") 
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")
HEADER_TEMPERATURE_KEY = config.get("header_temperature_key")

# Instance
PIXINSIGHT_INSTANCE = config.get("pixinsight_instance")
EXIT_INSTANCE = config.get("exit_instance")


def find_best_master(master_dir: Path, target_meta:dict, date_obs, max_days=None, max_months=None, 
                     ignore_expt=False, ignore_rot=False, ignore_temp=False, ignore_filter=False,
                     master_fmt="xisf", print_progress=False):
    """
    Selects the best master calibration frame (flat/dark) based on metadata and timing.
    """
    best_file = None
    master_fmt = master_fmt.lower().strip(".")
    nfiles = len(list(master_dir.glob(f"*.{master_fmt}")))
    i = 0
    for master_file in master_dir.glob(f"*.{master_fmt}"):
        if print_progress:
            print(f"Comparing file {i} / {nfiles}" + " " * 4, end='\r')
            i += 1
        try:
            if master_fmt != "fits" and master_fmt != "xisf":
                logger.error(f"Unsupported master format: {master_fmt}. Must be 'fits' or 'xisf'.")
                continue
            fits_dict = read_image_header_info(master_file, fmt=master_fmt)
            m_date = parse_date_from_master_path(master_file)
        except Exception as e:
            logger.error(f"Error reading master header {master_file}: {e}")
            continue
        
        # Metadata extraction   
        filt = fits_dict.get(HEADER_FILTER_KEY)
        expt = fits_dict.get(HEADER_EXPTIME_KEY)
        rot = fits_dict.get(HEADER_ROTATION_KEY)
        gain = fits_dict.get(HEADER_GAIN_KEY)
        offset = fits_dict.get(HEADER_OFFSET_KEY)
        temp = fits_dict.get(HEADER_TEMPERATURE_KEY)
        
        # target metadata
        tfilt = target_meta.get(HEADER_FILTER_KEY)
        texpt = target_meta.get(HEADER_EXPTIME_KEY)
        trot = target_meta.get(HEADER_ROTATION_KEY)
        tgain = target_meta.get(HEADER_GAIN_KEY)
        toffset = target_meta.get(HEADER_OFFSET_KEY)
        ttemp = target_meta.get(HEADER_TEMPERATURE_KEY)
        
        # Metadata checks
        # logger.debug(f"Checking master {master_file} with metadata: {fits_dict} vs target {target_meta}")
        reasons = []
        if not ignore_expt and expt != texpt:
            reasons.append(f"expt {expt} ({type(expt)}) != {texpt} ({type(texpt)})")
            # continue
        if not ignore_filter and filt is not None and tfilt is not None and filt != tfilt:
            reasons.append(f"filter {filt} ({type(filt)}) != {tfilt} ({type(tfilt)})")
            # continue
        if gain != tgain or offset != toffset:
            reasons.append(f"gain/offset {gain}/{offset} ({type(gain)}/{type(offset)}) != {tgain}/{toffset} ({type(tgain)}/{type(toffset)})")
            # continue
        if not ignore_temp and temp is not None and ttemp is not None and temp != ttemp:
            reasons.append(f"temp {temp} ({type(temp)}) != {ttemp} ({type(ttemp)})")
            # continue
        if not ignore_rot and rot is not None and trot is not None and abs(rot - trot) > ROT_TOLERANCE:
            reasons.append(f"rot {rot} ({type(rot)}) != {trot} ({type(trot)})")
            # continue
        
        if reasons:
            # logger.debug(f"Skipping master {master_file} due to metadata mismatch: " + "; ".join(reasons))
            continue

        # Timing checks
        # logger.debug(f"Checking master {master_file} with date {m_date} against target date {date_obs}")
        # logger.debug(f"Time diff: {abs((m_date - date_obs).days)} days. Max= {max_days} days. Max months= {max_months} months.")
        if max_days is not None and abs((m_date - date_obs).days) <= max_days:
            best_file = master_file
        if max_months is not None:
            month_diff = abs((m_date.year - date_obs.year) * 12 + (m_date.month - date_obs.month))
            if month_diff <= max_months:
                best_file = master_file

    return best_file

def find_best_masterv2(
    master_dir: Path,
    target_meta: dict,
    date_obs,
    max_days=None,
    max_months=None,
    ignore_expt: bool = False,
    ignore_rot: bool = False,
    ignore_temp: bool = False,
    ignore_filter: bool = False,
    master_fmt: str = "xisf",
    print_progress: bool = False,
):
    """
    Vectorized (pandas) master selection with the SAME signature and return type as find_best_master().

    Differences vs v1:
      - Uses a Parquet/pandas index instead of reading headers per-file.
      - Returns Path or None, same as v1.

    Requirements:
      - A master index parquet file must exist in master_dir:
          master_dir / "master_index.parquet"
        with at least columns:
          ["path","fmt","date","filter","gain","offset","exptime","temp","rot"]
      - 'date' must be datetime64-like, others typed or coercible.

    Notes:
      - This function intentionally *does not* scan the directory for progress printing;
        print_progress is accepted for compatibility but does nothing.
    """
    # ---- Load index ----
    idx_path = master_dir / "master_index.parquet"
    if not idx_path.exists():
        logger.error(f"Master index not found: {idx_path}")
        return None

    try:
        masters = pd.read_parquet(idx_path)
    except Exception as e:
        logger.error(f"Failed to read master index parquet {idx_path}: {e}")
        return None
    
    logger.debug(f"# files originally: {masters.shape[0]}")

    # ---- Normalize format selection ----
    master_fmt = (master_fmt or "xisf").lower().strip(".")
    if master_fmt not in ("fits", "xisf"):
        logger.error(f"Unsupported master format: {master_fmt}. Must be 'fits' or 'xisf'.")
        return None

    if "fmt" in masters.columns:
        masters = masters[masters["fmt"].astype("string").str.lower() == master_fmt]
        if masters.empty:
            return None
        
    logger.debug(f"# XISF files: {masters.shape[0]}")

    # ---- Pull target values (using your configured header keys) ----
    def _as_int(v):
        if v is None:
            return None
        try:
            return int(float(v))
        except Exception:
            return None

    def _as_float(v):
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    tfilt = target_meta.get(HEADER_FILTER_KEY)
    tgain = _as_int(target_meta.get(HEADER_GAIN_KEY))
    toffset = _as_int(target_meta.get(HEADER_OFFSET_KEY))
    texpt = _as_float(target_meta.get(HEADER_EXPTIME_KEY))
    ttemp = _as_float(target_meta.get(HEADER_TEMPERATURE_KEY))
    trot = _as_float(target_meta.get(HEADER_ROTATION_KEY))

    if tfilt is not None:
        tfilt = str(tfilt).upper().strip()

    # Required keys: gain/offset (matches v1 logic)
    if tgain is None or toffset is None:
        return None

    # ---- Coerce master columns to safe comparable types ----
    # (Avoids string-vs-int mismatches that broke v1 with XISF.)
    df = masters.copy()

    # Ensure date is datetime
    if "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna()]
    if df.empty:
        return None
    
    logger.debug(f"# files with valid dates: {df.shape[0]}")
    
    # Coerce numerics
    for col in ("gain", "offset"):
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("exptime", "temp", "rot"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "filter" in df.columns:
        df["filter"] = df["filter"].astype("string").str.upper()

    # ---- Metadata filters (same semantics as v1) ----
    # gain/offset exact match
    df = df[(df["gain"] == tgain) & (df["offset"] == toffset)]
    if df.empty:
        logger.debug("Gain or Offset don't match.")
        return None
    logger.debug(f"# files with correct gain + offset: {df.shape[0]}")

    # filter match only if both are not None and ignore_filter is False
    if (not ignore_filter) and (tfilt is not None) and ("filter" in df.columns):
        # v1 only checks mismatch if both filt and tfilt are not None
        df = df[df["filter"].notna() & (df["filter"] == tfilt)]
        if df.empty:
            logger.debug("Filter doesn't match")
            return None
        logger.debug(f"# files with correct filter: {df.shape[0]}")

    # exposure match only if ignore_expt is False (v1 compares even if None, but your v1
    # effectively expects expt/texpt present; we mirror behavior by only filtering if texpt not None)
    if (not ignore_expt) and (texpt is not None) and ("exptime" in df.columns):
        df = df[df["exptime"].notna() & (df["exptime"] == texpt)]
        if df.empty:
            return None
        logger.debug(f"# files with correct : {df.shape[0]}")


    # temp match only if both non-None and ignore_temp is False (same as v1)
    if (not ignore_temp) and (ttemp is not None) and ("temp" in df.columns):
        # v1 only rejects if temp is not None and ttemp is not None and temp != ttemp
        df = df[df["temp"].isna() | (df["temp"] == ttemp)]
        if df.empty:
            return None
        logger.debug(f"# files with correct temperature: {df.shape[0]}")

    # rot match only if both non-None and ignore_rot is False (same as v1)
    if (not ignore_rot) and (trot is not None) and ("rot" in df.columns):
        # v1 only rejects if rot and trot both not None and abs(rot-trot)>tol
        df = df[df["rot"].isna() | ((df["rot"] - trot).abs() <= ROT_TOLERANCE)]
        if df.empty:
            logger.debug("Rotation doesn't match.")
            return None
        logger.debug(f"# files with correct rotation: {df.shape[0]}")
        
    logger.debug(f"Files left: {df}")

    # ---- Timing window & selection (match v1’s “last in loop wins” behavior) ----
    # v1 sets best_file whenever a candidate falls within window, and because it iterates
    # in filesystem order, the *last* matching candidate wins (not “closest”).
    #
    # To replicate that deterministically, we:
    #   - compute the within-window boolean
    #   - keep only rows within window
    #   - pick the row with the *largest* sort key representing “last in glob order”
    #
    # Since we no longer iterate files, we need a stable proxy for glob order.
    # We'll use path (string) ascending as the “order”; then “last” = max(path).
    # If you prefer most-recent date instead, change sort below.
    #
    # If neither max_days nor max_months is provided, v1 would never set best_file;
    # we keep that behavior (return None).
    if max_days is None and max_months is None:
        return None

    date_obs_ts = pd.Timestamp(date_obs)

    if max_days is not None:
        df_date_index = df["date"].dt.date
        ref_date = date_obs_ts.date()
        date_diff = (df_date_index - ref_date).abs()
        within = date_diff <= pd.Timedelta(days=int(max_days))
        df = df[within]
        logger.debug(f"# files within accepted window: {df.shape[0]}")
        if df.empty:
            return None
        # emulate “last match wins”: pick max(path)
        # df = df.sort_values("path", ascending=True)
        # return Path(df.iloc[-1]["path"])
        # ALTERNATIVE: pick the one that is closest in time (minimize days diff)
        best = df.loc[(df["date"] - date_obs_ts).abs().idxmin()]
        return Path(best["path"])

    # max_months is not None
    df_month_index = df["date"].dt.year * 12 + df["date"].dt.month
    ref_month_index = date_obs_ts.year * 12 + date_obs_ts.month

    month_diff = (df_month_index - ref_month_index).abs()
    within = month_diff <= int(max_months)
    df = df[within]
    if df.empty:
        return None
    logger.debug(f"# files within accepted window: {df.shape[0]}")
    # df = df.sort_values("path", ascending=True)
    # return Path(df.iloc[-1]["path"])
    # ALTERNATIVE: pick the one that is closest in time (minimize months diff)
    best = df.loc[month_diff.idxmin()]
    return Path(best["path"])

def calibrate_lights(
    light_dir: Path,
    flats_dir: Path,
    max_flat_days_diff: int,
    darks_dir: Path,
    max_dark_months_diff: int,
    output_dir: Path,
    instance: int,
    overwrite_existing: bool,
    exit_instance: bool
    ):
    """
    Calibrates light frames using the best-matching master flats and darks.
    
    Stores a log file with the format:
    {
        "Targets name": "Target1",
        "DD-MM-YYYY": {
            "Filter1": {
                "Expt1": {
                    "flat_master": "path/to/masterFlat.xisf",
                    "dark_master": "path/to/masterDark.xisf",
                    "num calibrated": 5,
                    "failed lights": ["path/to/failed1.fits", ...]
                },
                ...
            },
            ...
        },
    }
    """
    calibration_groups = defaultdict(list)

    # Build output path
    fits = list(light_dir.rglob("*.fits"))
    if len(fits) == 0:
        logger.warning(f"No FITS files found in {light_dir}. Skipping calibration.")
        return []
    out_path = output_dir / get_target_name(fits[0])
    ensure_dir(out_path)
    
    nframes = len(list(light_dir.rglob("*.fits")))
    for fits_file in light_dir.rglob("*.fits"):
        
        print(f"\nProcessing light frame {fits.index(fits_file)+1} / {nframes}")
        
        try:
            fits_dict = read_image_header_info(fits_file, fmt="auto")
            date_obs = parse_date_from_path(fits_file) or None
            logger.debug(f"Processing light frame: {fits_file} with date {date_obs}")
        except Exception as e:
            logger.error(f"Error reading light header {fits_file}: {e}")
            continue

        # checking if already calibrated somewhere in the directory (not necessarily in the parent directory)
        calibrated_file_fits = fits_file.name.replace(".fits", f'__{date_obs.strftime("%d%m%Y")}_c.fits')
        calibrated_file_xisf = fits_file.name.replace(".fits", f'__{date_obs.strftime("%d%m%Y")}_c.xisf')
        if not overwrite_existing and (file_exists_in_tree(calibrated_file_fits, out_path) or file_exists_in_tree(calibrated_file_xisf, out_path)):
            logger.debug(f"Skipping already calibrated file: {fits_file}")
            continue
        
        # Metadata extraction
        filt = fits_dict.get(HEADER_FILTER_KEY)
        rot = fits_dict.get(HEADER_ROTATION_KEY)
        gain = fits_dict.get(HEADER_GAIN_KEY)
        offset = fits_dict.get(HEADER_OFFSET_KEY)
        expt = fits_dict.get(HEADER_EXPTIME_KEY)
        temp = fits_dict.get(HEADER_TEMPERATURE_KEY)


        flat_meta = {
            HEADER_FILTER_KEY: filt,
            HEADER_GAIN_KEY: gain,
            HEADER_OFFSET_KEY: offset,
            HEADER_ROTATION_KEY: rot
        }
        flat_master = find_best_masterv2(
            flats_dir, flat_meta, date_obs,
            max_days=max_flat_days_diff, ignore_expt=True, ignore_temp=True,
            print_progress=True
        )
        logger.debug(f"Selected flat master for {fits_file}: {flat_master}")
        
        dark_meta = {
            HEADER_GAIN_KEY: gain,
            HEADER_OFFSET_KEY: offset,
            HEADER_EXPTIME_KEY: expt,
            HEADER_TEMPERATURE_KEY: temp
        }
        
        dark_master = find_best_masterv2(
            darks_dir, dark_meta, date_obs,
            max_months=max_dark_months_diff, ignore_rot=True, ignore_filter=True,
            print_progress=True
        )
        logger.debug(f"Selected dark master for {fits_file}: {dark_master}")

        # if max_cal_time_diff is None, we don't require that master
        if (not flat_master and max_flat_days_diff is not None) or (not dark_master and max_dark_months_diff is not None):
            logger.debug(f"Skipping calibration for {fits_file}: missing master (flat={flat_master}, dark={dark_master})")
            continue
        
        # grouping by (flat, dark, outpath)
        calibration_groups[(flat_master, dark_master, out_path, expt, filt, date_obs)].append((fits_file))
        
        
    # Get the total number of files for a given out_path
    targets = {out_path:  out_path / "calibration_log.json"  for (_, _, out_path, _, _, _), files in calibration_groups.items()}
    # Create a log file in the output directory for each target: out_path/calibration_log.json
    for target_path, calib_path in targets.items():
        if not calib_path.exists():
            with open(calib_path, 'w') as f:
                json.dump({"Target name": out_path.name}, f, indent=2)
                
                
    force_new_inst = True
    if instance is not None and instance < 0:
        force_new_inst = False
        
    # launch pixinsight
    process = None
    if force_new_inst and len(calibration_groups) > 0:  
        process = launch_pixinsight(instance=instance)

    # Process each group
    for (flat_master, dark_master, out_path, expt, filt, date_obs), light_files in calibration_groups.items():
        logger.info(f"Calibrating {len(light_files)} {expt}s {filt} lights with flat={flat_master}, dark={dark_master}")
        
        # update calibration log
        calib_log_path = out_path / "calibration_log.json"
        with open(calib_log_path, 'r+') as f:
            log_data = json.load(f)
        
        # Initialize nested structure, if needed
        light_date = date_obs.strftime("%d-%m-%Y")
        if light_date not in log_data.keys():
            log_data[light_date] = {}
        log_date = log_data[light_date]
        if filt not in log_date:
            log_data[light_date][filt] = {}
        log_filter = log_data[light_date][filt]
        if expt not in log_filter:
            log_data[light_date][filt][expt] = {
                "flat_master": str(flat_master) if flat_master else None,
                "dark_master": str(dark_master) if dark_master else None,
                "num calibrated": 0,
                "failed lights": []
            }
        log_expt = log_data[light_date][filt][expt]
            
        # running calibration on light group
        calibrate_with_pixinsight(light_files, 
                                  flat_master, 
                                  dark_master,
                                  out_path,
                                  date_obs,
                                  instance=instance)

        for fits_file in light_files:
            calibrated = True
            calibrated_file_fits = out_path / fits_file.name.replace(".fits", f'__{date_obs.strftime("%d%m%Y")}_c.fits')
            calibrated_file_xisf = out_path / fits_file.name.replace(".fits", f'__{date_obs.strftime("%d%m%Y")}_c.xisf')
            print(f"Expected:   {calibrated_file_xisf}")
            if calibrated_file_fits.exists():
                logger.debug(f"Calibrated: {fits_file} -> {calibrated_file_fits}")
            elif calibrated_file_xisf.exists():
                logger.debug(f"Calibrated: {fits_file} -> {calibrated_file_xisf}")
            else:
                logger.error(f"Calibration failed for {fits_file}")
                calibrated = False
                log_expt["failed lights"].append(str(fits_file))
                continue
            log_expt["num calibrated"] += 1 if calibrated else 0
    
        # save updated calibration log
        with open(calib_log_path, 'r+') as f:
            f.seek(0)
            json.dump(log_data, f, indent=4)
            f.truncate()
            
    # exit pixinsight instance
    if exit_instance and process is not None:
        close_pixinsight(process)

    # Return paths to directories with new/updated calibration logs
    return [out_path for (_, _, out_path, _, _, _), _ in calibration_groups.items() if out_path.exists()]
            
            

    

def main():
    logger.info("Starting calibration process...")
    if TARGET_NAME is None:
        logger.error("TARGET_NAME must be specified in config.yaml. Exiting.")
        return
    
    target_dir = LIGHT_BASE_DIR / TARGET_NAME
    if not target_dir.exists():
        logger.error(f"Target directory {target_dir} does not exist. Exiting.")
        return
    
    
    calibrate_lights(target_dir, 
                     FLATS_DIR, 
                     MAX_FLAT_DAYS_DIFF,
                     DARKS_DIR, 
                     MAX_DARK_MONTHS_DIFF,
                     CALIBRATED_OUTPUT_DIR, 
                     PIXINSIGHT_INSTANCE,
                     OVERWRITE_EXISTING,
                     EXIT_INSTANCE
                     )
    logger.info("Calibration process finished.")

    # plot summary for each output directory
    target_out_dir = CALIBRATED_OUTPUT_DIR / get_target_name(target_dir)
    calib_log_path = target_out_dir / "calibration_log.json"
    if calib_log_path.exists():
        plot_summary(calib_log_path)
        logger.info(f"Summary plot saved for {target_out_dir}")


if __name__ == "__main__":
    main()