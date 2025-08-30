#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict
from time import sleep, time
from astro_utils.config_loader import load_config
from astro_utils.fits_helpers import read_fits_header_info, parse_date_from_path, parse_date_from_master_path
from astro_utils.pixinsight_cli import launch_pixinsight, calibrate_with_pixinsight
from astro_utils.file_ops import ensure_dir, get_target_name
from astro_utils.plotting import plot_summary
import logging
import json

# --- Logging ---
logger = logging.getLogger(__name__)

# --- Load config ---
config = load_config(Path(__file__).parent / "config.yaml")

LIGHT_BASE_DIR = Path(config.get("light_base_dir", "D:/Astrophotography/SkyShare Data/LIGHT"))
CALIBRATED_OUTPUT_DIR = Path(config.get("calibrated_output_dir", "D:/Astrophotography/SkyShare Data/CALIBRATED"))
FLATS_DIR = Path(config.get("flat_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/FLAT"))
DARKS_DIR = Path(config.get("dark_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/DARK"))

MAX_FLAT_DAYS_DIFF = config.get("calibration_search_days_flat", 4)
MAX_DARK_MONTHS_DIFF = config.get("calibration_search_months_dark", 3)
ROT_TOLERANCE = config.get("flat_rotation_tolerance", 0.5)
OVERWRITE_EXISTING = config.get("overwrite_existing", False)

# FITS keywords
HEADER_FILTER_KEY = config.get("header_filter_key")
HEADER_ROTATION_KEY = config.get("header_rotation_key")
HEADER_GAIN_KEY = config.get("header_gain_key") 
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")
HEADER_TEMPERATURE_KEY = config.get("header_temperature_key")

# Instance
PIXINSIGHT_INSTANCE = config.get("pixinsight_instance")


def find_best_master(master_dir: Path, target_meta:dict, date_obs, max_days=None, max_months=None, 
                     ignore_expt=False, ignore_rot=False, ignore_temp=False, ignore_filter=False):
    """
    Selects the best master calibration frame (flat/dark) based on metadata and timing.
    """
    best_file = None
    for master_file in master_dir.glob("*.fits"):
        try:
            fits_dict = read_fits_header_info(master_file)
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

        # Metadata checks
        # logger.debug(f"Checking master {master_file} with metadata: {fits_dict} vs target {target_meta}")
        if not ignore_expt and expt != target_meta[HEADER_EXPTIME_KEY]:
            continue
        if not ignore_filter and filt != target_meta[HEADER_FILTER_KEY]:
            continue
        if gain != target_meta[HEADER_GAIN_KEY] or offset != target_meta[HEADER_OFFSET_KEY]:
            continue
        if not ignore_temp and temp is not None and temp != target_meta[HEADER_TEMPERATURE_KEY]:
            continue
        if not ignore_rot and rot is not None and abs(rot - target_meta[HEADER_ROTATION_KEY]) > ROT_TOLERANCE:
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


def calibrate_lights(
    light_dir: Path,
    flats_dir: Path,
    darks_dir: Path,
    output_dir: Path,
    instance=PIXINSIGHT_INSTANCE):
    """
    Calibrates light frames using the best-matching master flats and darks.
    
    Stores a log file with the format:
    {
        "Targets name": "Target1",
        "DD-MM-YYYY": {
            "Filter1": {
                "Expt1": {
                    "flat_master": "path/to/masterFlat.fits",
                    "dark_master": "path/to/masterDark.fits",
                    "num lights": 10,
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

    for fits_file in light_dir.rglob("*.fits"):
        try:
            fits_dict = read_fits_header_info(fits_file)
            date_obs = parse_date_from_path(fits_file) or None
            # logger.debug(f"Processing light frame: {fits_file} with date {date_obs}")
        except Exception as e:
            logger.error(f"Error reading light header {fits_file}: {e}")
            continue
        
        # Metadata extraction
        filt = fits_dict.get(HEADER_FILTER_KEY)
        rot = fits_dict.get(HEADER_ROTATION_KEY)
        gain = fits_dict.get(HEADER_GAIN_KEY)
        offset = fits_dict.get(HEADER_OFFSET_KEY)
        expt = fits_dict.get(HEADER_EXPTIME_KEY)
        temp = fits_dict.get(HEADER_TEMPERATURE_KEY)

        # Build output path
        out_path = output_dir / get_target_name(fits_file)

        ensure_dir(out_path)

        flat_meta = {
            HEADER_FILTER_KEY: filt,
            HEADER_GAIN_KEY: gain,
            HEADER_OFFSET_KEY: offset,
            HEADER_ROTATION_KEY: rot
        }
        flat_master = find_best_master(
            flats_dir, flat_meta, date_obs,
            max_days=MAX_FLAT_DAYS_DIFF, ignore_expt=True, ignore_temp=True
        )
        
        dark_meta = {
            HEADER_GAIN_KEY: gain,
            HEADER_OFFSET_KEY: offset,
            HEADER_EXPTIME_KEY: expt,
            HEADER_TEMPERATURE_KEY: temp
        }
        dark_master = find_best_master(
            darks_dir, dark_meta, date_obs,
            max_months=MAX_DARK_MONTHS_DIFF, ignore_rot=True, ignore_filter=True
        )

        if not flat_master or not dark_master:
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
    if force_new_inst:  
        launch_pixinsight(instance=instance)

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
                "flat_master": str(flat_master),
                "dark_master": str(dark_master),
                "num lights": 0,
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
        with open(calib_log_path, 'w') as f:
            json.dump(log_data, f, indent=4)
    
    # Return paths to directories with new/updated calibration logs
    return [out_path for (_, _, out_path, _, _, _), _ in calibration_groups.items() if out_path.exists()]
            
            

    

def main():
    logger.info("Starting calibration process...")
    calibrate_lights(LIGHT_BASE_DIR, FLATS_DIR, DARKS_DIR, CALIBRATED_OUTPUT_DIR)
    logger.info("Calibration process finished.")

    # plot summary for each output directory
    target_out_dir = CALIBRATED_OUTPUT_DIR / get_target_name(LIGHT_BASE_DIR)
    calib_log_path = target_out_dir / "calibration_log.json"
    if calib_log_path.exists():
        plot_summary(calib_log_path)
        logger.info(f"Summary plot saved for {target_out_dir}")


if __name__ == "__main__":
    main()