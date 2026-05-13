"""
Creates master index parquets for FLATS and DARKS
"""

import logging
from pathlib import Path
from astro_utils.config_loader import load_config
from astro_utils.fits_helpers import build_master_index_parquet
# --- Logging ---
logger = logging.getLogger(__name__)
# --- Load config ---
config = load_config(Path(__file__).parent / "config.yaml")
LOG_LEVEL = str(config.get("log_level")).upper()
logger.setLevel(LOG_LEVEL)
# --- Paths ---
FLAT_MASTER_DIR = Path(config.get("flat_master_output"))
DARK_MASTER_DIR = Path(config.get("dark_master_output"))

def main():
    flat_index_path = FLAT_MASTER_DIR / "master_index.parquet"
    dark_index_path = DARK_MASTER_DIR / "master_index.parquet"

    logger.info(f"Building master flats index parquet at: {flat_index_path}")
    build_master_index_parquet(FLAT_MASTER_DIR, flat_index_path, type="FLAT", verbose=True)

    logger.info(f"Building master darks index parquet at: {dark_index_path}")
    build_master_index_parquet(DARK_MASTER_DIR, dark_index_path, type="DARK", verbose=True)
    
if __name__ == "__main__":
    main()