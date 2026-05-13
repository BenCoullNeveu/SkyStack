# given a directory, convert all FITS files to XISF format

from pathlib import Path
from astro_utils.file_ops import ensure_dir
from astro_utils.fits_helpers import xisf2fits


DIRECTORIES = { 
    "D:\\Astrophotography\\SkyShare Data\\CALIBRATED\\Leo Triplet\\BLINKED" :
        "D:\\Astrophotography\\SkyShare Data\\CALIBRATED\\Leo Triplet\\BLINKED_FITS"
}   

for indir, outdir in DIRECTORIES.items():
    inpath = Path(indir)
    ensure_dir(inpath)
    outpath = inpath
    if outdir is not None:
        outpath = Path(outdir)
        ensure_dir(outpath)
        xisf_files = list(inpath.rglob("*.xisf"))
    for i, xisf_file in enumerate(xisf_files):
        print(f"Converting {xisf_file}  |  {i+1}/{len(xisf_files)}")
        fits_file = xisf_file.with_suffix(".fits") if outdir is None else outpath / xisf_file.relative_to(inpath).with_suffix(".fits")
        # input("Press Enter to continue...")
        xisf2fits(xisf_file, fits_file, delete_after_convert=False)