# given a directory, convert all FITS files to XISF format

from pathlib import Path
from astro_utils.file_ops import ensure_dir
from astro_utils.fits_helpers import fits2xisf


DIRECTORIES = [
    r"D:\Dropbox\SKYSHARE\CLIENT DATA\North America Nebula Ross Wynne\RAWS\masterCalibrationFiles",
]

for directory in DIRECTORIES:
    dir_path = Path(directory)
    ensure_dir(dir_path)
    for fits_file in dir_path.rglob("*.fits"):
        print(f"Converting {fits_file}...")
        xisf_file = fits_file.with_suffix(".xisf")
        # input("Press Enter to continue...")
        fits2xisf(fits_file, xisf_file, delete_after_convert=False)