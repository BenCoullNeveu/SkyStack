"""
Clean `local_target` directory by removing old raw files, and removing empty directories
"""

import os
from pathlib import Path
import shutil
import sys
import logging
import time
from astro_utils.config_loader import load_config

config_path = Path(__file__).parent / "config.yaml"
config = load_config(config_path)

# extract config values
LOCAL_TARGET = Path(config.get("local_target"))
SUBDIRS = config.get("subdirs")
DELETE_AFTER_TRANSFER = config.get("delete_after_transfer")
REMOVE_EMPTY_DIRS = config.get("remove_empty_dirs")
REVERSE_TRANSFER = config.get("reverse_transfer")
SPECIFIC_DIR_KEYWORDS = config.get("specific_dir_keywords", [])

print(f"Raw Directory: {LOCAL_TARGET} >>> EXISTS: {LOCAL_TARGET.exists()}")

def main():
    pass


if __name__ == "__main__":
    main()