"""
star_metrics.py

Star morphology metrics for guiding and quality assessment.
"""

import numpy as np


def compute_star_metrics(objects):
    """
    Computes aggregate star statistics.

    Returns None if insufficient stars.
    """

    if objects is None or len(objects) < 10:
        return None

    a = objects["a"]
    b = objects["b"]

    fwhm = 2.355 * np.sqrt(a * b)
    ellipticity = 1.0 - (b / a)

    return {
        "n_stars": len(objects),
        "median_fwhm": float(np.median(fwhm)),
        "fwhm_std": float(np.std(fwhm)),
        "median_ellipticity": float(np.median(ellipticity)),
    }
