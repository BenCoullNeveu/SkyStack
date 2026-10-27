"""
star_detection.py

Star detection utilities.
"""

import sep


def detect_stars(image, threshold=5.0):
    """
    Detect stars in an image using SEP.

    Returns
    -------
    sep_objects or None
    """

    bkg = sep.Background(image)
    data = image - bkg

    objects = sep.extract(
        data,
        thresh=threshold,
        err=bkg.globalrms
    )

    if len(objects) == 0:
        return None

    return objects
