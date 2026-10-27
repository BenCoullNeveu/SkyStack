# given a directory, convert all FITS files to XISF format

from pathlib import Path
from astro_utils.file_ops import ensure_dir
from astro_utils.fits_helpers import xisf2fits


DIRECTORIES = [
    r"D:\Dropbox\SKYSHARE\CLIENT DATA\NGC 7000 Terry Hanks\RAWS\masterCalibrationFiles"
]

for directory in DIRECTORIES:
    dir_path = Path(directory)
    ensure_dir(dir_path)
    for xisf_file in dir_path.rglob("*.xisf"):
        print(f"Converting {xisf_file}...")
        fits_file = xisf_file.with_suffix(".fits")
        # input("Press Enter to continue...")
        xisf2fits(xisf_file, fits_file, delete_after_convert=False)