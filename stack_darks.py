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
BASE_DIR = Path(config.get("dark_base_dir"))
MASTER_OUTPUT = Path(config.get("dark_master_output"))

# FITS keywords
HEADER_GAIN_KEY = config.get("header_gain_key", "GAIN")
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")
HEADER_TEMPERATURE_KEY = config.get("header_temperature_key")

# tolerance configs
MAX_DAYS_DIFF = config.get("dark_search_days")

# Pixinsight instance
PIXINSIGHT_INSTANCE = config.get("pixinsight_instance")

group_params = [HEADER_EXPTIME_KEY, 
                HEADER_GAIN_KEY, 
                HEADER_OFFSET_KEY,
                HEADER_TEMPERATURE_KEY]

groups = group_by_settings(BASE_DIR, group_params)

logger.debug(f"Found {len(groups)} groups of dark frames.")

master_name_fmt = {
    HEADER_EXPTIME_KEY: (int, "s"),
    HEADER_TEMPERATURE_KEY: (int, "C"),
    HEADER_GAIN_KEY: (int, "gain")
}
stack(groups, MASTER_OUTPUT, "dark", master_name_fmt,
      max_days_diff=MAX_DAYS_DIFF,
      instance=PIXINSIGHT_INSTANCE)