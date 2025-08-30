#!/usr/bin/env python3
import argparse
from pathlib import Path
from collections import defaultdict
from astro_utils.config_loader import load_config
from astro_utils.fits_helpers import read_fits_header_info, parse_date_from_path, get_val, write_header_info
from astro_utils.pixinsight_cli import launch_pixinsight, stack_with_pixinsight
from astro_utils.file_ops import ensure_dir
import logging
import shutil
import os

logger = logging.getLogger(__name__)

# Get config
config = load_config(Path(__file__).parent / "config.yaml")
LOG_LEVEL = str(config.get("log_level", "INFO")).upper()
logger.setLevel(LOG_LEVEL)
HEADER_FILTER_KEY = config.get("header_filter_key")
HEADER_ROTATION_KEY = config.get("header_rotation_key")
HEADER_GAIN_KEY = config.get("header_gain_key")
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")
HEADER_TEMPERATURE_KEY = config.get("header_temperature_key") 
HEADER_IMGTYPE_KEY = config.get("header_img_type_key")

def move_to_used(file: Path):
    used_dir = file.parent / "USED"
    used_dir.mkdir(exist_ok=True)
    dest = used_dir / file.name
    try:
        file.rename(dest)
    except OSError:
        shutil.move(str(file), str(dest))  # fallback for cross-device move

def group_by_settings(dir: Path, header_keys=None):
    groups = defaultdict(list)
    for fits_file in dir.rglob("*.fits"):
        if "USED" not in fits_file.parts:
            logger.debug(f"Processing {fits_file}")
            try:
                fits_dict = read_fits_header_info(fits_file)
                date_obs = parse_date_from_path(fits_file)
                
                if date_obs is None:
                    continue
                
                key_dict = {}
                for k in header_keys:
                    key_dict[k] = fits_dict.get(k)
                key_dict["date"] = date_obs.strftime("%Y-%m-%d")
                key = tuple(key_dict.items())
                groups[key].append((fits_file, date_obs))
                
            except OSError:
                logger.error(f"OSError: likely corrupt file: {fits_file}")
            except Exception as e:
                logger.error(f"Error processing {fits_file}: {e}")
    return groups

def safe_fmt(value, fmt=str):
    if value is None:
        return "NA"
    try:
        return fmt(value)
    except Exception:
        return str(value)

def stack(groups: dict, output_dir: Path, master_name_pref:str, master_name_fmt:dict, 
          max_days_diff=0, rot_tolerance=None,
          instance=None):
    """master_name_fmt os a dict where the keys are the unit and the values are tuples of the format strings and units. 
    DO NOT INCLUDE DATE! This will be added automatically following a specific format, i.e. `__%d%m%Y`.
    master_name_pref is a prefix for the master name. E.g., for `flat`, we get `masterFlat`."""
    
    force_new_inst = True
    if instance is not None and instance < 0:
        force_new_inst = False
        instance = None
        
    # launch pixinsight
    if force_new_inst:
        launch_pixinsight(instance=instance)

    ensure_dir(output_dir)
    ngroups = len(groups)
    i = 1
    for key, files_dates in groups.items():
        logger.debug(f"Processing group {i}/{ngroups} with {len(files_dates)} files.")
        i += 1
        files_dates.sort(key=lambda x: x[1])
        while files_dates:
            base_date = files_dates[0][1]
            batch = [fd[0] for fd in files_dates if abs((fd[1] - base_date).days) <= max_days_diff]
            if rot_tolerance is not None:
                rot = get_val(HEADER_ROTATION_KEY, key)
                if rot is not None:
                    batch = [f for f in batch if abs(read_fits_header_info(f)[HEADER_ROTATION_KEY] - rot) <= rot_tolerance]

            logger.debug(f"Processing batch for key {key} with {len(batch)} files.")
            files_dates = [fd for fd in files_dates if fd[0] not in batch]
            
            # Format master name using provided template
            master_name = "master" + master_name_pref.lower().capitalize()
            for k, v in master_name_fmt.items():
                master_name += f"_{safe_fmt(get_val(k, key), v[0])}{v[1]}"
            master_name += f"__{base_date.strftime('%d%m%Y')}.fits"            
            
            out_path = output_dir / master_name
            stack_with_pixinsight(batch, 
                                  out_path, 
                                  instance=instance)
            # check if file was created
            if out_path.exists():
                # Add necessary FITS header info
                fits_dict = read_fits_header_info(batch[0])
                header_dict = {
                    HEADER_FILTER_KEY: fits_dict.get(HEADER_FILTER_KEY),
                    HEADER_ROTATION_KEY: fits_dict.get(HEADER_ROTATION_KEY),
                    HEADER_GAIN_KEY: fits_dict.get(HEADER_GAIN_KEY),
                    HEADER_OFFSET_KEY: fits_dict.get(HEADER_OFFSET_KEY),
                    HEADER_EXPTIME_KEY: fits_dict.get(HEADER_EXPTIME_KEY),
                    HEADER_TEMPERATURE_KEY: fits_dict.get(HEADER_TEMPERATURE_KEY),
                    HEADER_IMGTYPE_KEY: f"Master {master_name_pref.capitalize()}"
                }
                logger.debug(f"Updating header for {out_path} with {header_dict}")
                write_header_info(out_path, header_dict)
                logger.debug(f"Created master {master_name_pref.lower()}: {out_path}")
                # Move files to USED
                for file in batch:
                    if "USED" not in file.parts:
                        move_to_used(file)
            else:
                logger.error(f"Failed to create master {master_name_pref.lower()}: {out_path}")
                break

                