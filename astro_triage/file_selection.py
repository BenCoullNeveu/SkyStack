"""
file_selection.py

Defines how LIGHT frames are grouped for triage.
"""

from pathlib import Path
from collections import defaultdict
from astro_utils.fits_helpers import read_fits_header_info, parse_date_from_path


def find_light_sequences(
    light_base_dir: Path,
    target_name: str,
    header_filter_key: str,
    header_exptime_key: str,
    header_gain_key: str,
    header_offset_key: str,
):
    """
    Returns a dict:
        {
          (date, filter, exptime, gain, offset): [fits1, fits2, ...]
        }
    """

    target_dir = light_base_dir / target_name
    if not target_dir.exists():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    sequences = defaultdict(list)

    for fits_file in target_dir.rglob("*.fits"):
        try:
            hdr = read_fits_header_info(fits_file)
            date_obs = parse_date_from_path(fits_file)
        except Exception:
            continue

        key = (
            date_obs.strftime("%Y-%m-%d") if date_obs else "UNKNOWN",
            hdr.get(header_filter_key),
            hdr.get(header_exptime_key),
            hdr.get(header_gain_key),
            hdr.get(header_offset_key),
        )

        sequences[key].append(fits_file)

    # Sort frames temporally within each sequence
    for key in sequences:
        sequences[key].sort()

    return sequences
