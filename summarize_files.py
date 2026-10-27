"""
summarize_files.py

organizes files by type and summarizes info in table format as shown:

# FLATS
Filter  Num Flats  Total File Size (MB)  Num Unused Raws (not used in existing masters)
-----   ---------  --------------------  ----------------------------------------------
L               50                  250                                    30
R               45                  225                                    30
...

# DARKS
Exposure (s)   Num Darks  Total File Size (MB)  Num Unused Raws (not used in existing masters)
-------------  ---------  --------------------  ----------------------------------------------
30                    40                  600                                    10       
...

# BIAS
Num Bias    Total File Size (MB)  Num Unused Raws (not used in existing masters)
--------  --------------------  ----------------------------------------------
100                 50                                    20
...     
                
# LIGHTS
Target Name    Num Lights    Total File Size (GB)     Num Uncalibrated Lights
-------------  ------------  ----------------------  ---------------------------
M31                  120                     36.0                         5
M42                   80                     24.0                         2
...



The file is saved as 'file_summary.txt' in the specified output directory.
we can then run clean_raws.py to remove unused raws based on this summary given specifics in config file.
"""

import os
from pathlib import Path
import shutil
import sys
import logging

import time
from astro_utils.config_loader import load_config
from astro_utils.file_ops import get_target_name
config_path = Path(__file__).parent / "config.yaml"
config = load_config(config_path)

# extract config values
LOCAL_TARGET = Path(config.get("local_target"))
SUBDIRS = config.get("subdirs")
OUTPUT_DIR = Path(config.get("output_dir", LOCAL_TARGET))
SUMMARY_FILE = OUTPUT_DIR / "file_summary.txt"
print(f"Output Directory: {OUTPUT_DIR} >>> EXISTS: {OUTPUT_DIR.exists()}")


def main():
    

if __name__ == "__main__":
    main()