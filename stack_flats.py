import logging
from pathlib import Path
from astro_utils.config_loader import load_config
from stack_calibration_frames import group_by_settings, stack

# --- Logging ---
logger = logging.getLogger(__name__)
# --- Load config ---
config = load_config(Path(__file__).parent / "config.yaml")
LOG_LEVEL = str(config.get("log_level")).upper()
logger.setLevel(LOG_LEVEL)

# --- Paths ---
BASE_DIR = Path(config.get("flat_base_dir", "D:/Astrophotography/SkyShare Data/FLAT"))
MASTER_OUTPUT = Path(config.get("flat_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/FLAT"))

# FITS keywords
HEADER_FILTER_KEY = config.get("header_filter_key")
HEADER_ROTATION_KEY = config.get("header_rotation_key")
HEADER_GAIN_KEY = config.get("header_gain_key", "GAIN")
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")

# tolerance configs
MAX_DAYS_DIFF = config.get("flat_search_days")
ROT_TOLERANCE = config.get("flat_rotation_tolerance")


group_params = [HEADER_FILTER_KEY, 
                HEADER_EXPTIME_KEY, 
                HEADER_GAIN_KEY, 
                HEADER_OFFSET_KEY, 
                HEADER_ROTATION_KEY]

groups = group_by_settings(BASE_DIR, group_params)

logger.debug(f"Found {len(groups)} groups of flats frames.")

master_name_fmt = {
    HEADER_FILTER_KEY: (str, ""),
    HEADER_EXPTIME_KEY: (int, "s"),
    HEADER_GAIN_KEY: (int, "gain"),
    HEADER_ROTATION_KEY: (int, "degrees")
}
stack(groups, MASTER_OUTPUT, "flat", master_name_fmt,
      max_days_diff=config.get("flat_search_days", 30),
      rot_tolerance=config.get("flat_rotation_tolerance", 0.5))