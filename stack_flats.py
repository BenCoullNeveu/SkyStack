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
BASE_DIR = Path(config.get("flat_base_dir"))
MASTER_OUTPUT = Path(config.get("flat_master_output"))
FLAT_INDEX_PATH = BASE_DIR / config.get("flat_index_file")

# To append or not
# Only the legacy parquet index is appended to here; with master_index "db"
# new masters are picked up by the skystack.db sync that follows stacking.
APPEND_TO_INDEX = (str(config.get("master_index", "db")).lower() == "parquet"
                   and not config.get("create_index_files", False))

# FITS keywords
HEADER_FILTER_KEY = config.get("header_filter_key")
HEADER_ROTATION_KEY = config.get("header_rotation_key")
HEADER_GAIN_KEY = config.get("header_gain_key", "GAIN")
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")

# tolerance configs
MAX_DAYS_DIFF = config.get("flat_search_days")
ROT_TOLERANCE = config.get("flat_rotation_tolerance")

# Pixinsight instance
PIXINSIGHT_INSTANCE = config.get("pixinsight_instance")
EXIT_INSTANCE = config.get("exit_instance")


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
      rot_tolerance=config.get("flat_rotation_tolerance", 0.5),
      instance=PIXINSIGHT_INSTANCE, 
      exit_instance=EXIT_INSTANCE,
      update_index=APPEND_TO_INDEX,
      index_path=FLAT_INDEX_PATH,
      type="FLAT") 