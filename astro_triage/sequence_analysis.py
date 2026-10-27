"""
sequence_analysis.py

Temporal transparency metrics with backend fallbacks.
"""

import numpy as np
from astro_triage.backend import xp, asnumpy


def frame_medians(images, backend="cpu"):
    """
    Returns per-frame median brightness.
    """

    if backend == "gpu":
        xp_mod = xp(backend)
        data = xp_mod.asarray(images)
        med = xp_mod.median(data, axis=(1, 2))
        return asnumpy(med)

    # CPU fallback
    return np.array([np.median(im) for im in images])


def transparency_drift(medians, backend="cpu"):
    """
    Global transparency variability.
    """

    if backend == "gpu":
        xp_mod = xp(backend)
        m = xp_mod.asarray(medians)
        drift = xp_mod.std(m - xp_mod.median(m))
        return float(drift)

    # CPU fallback
    med = np.median(medians)
    return float(np.std(medians - med))


def moving_variation(medians, window=5):
    if len(medians) < window:
        return 0.0

    kernel = np.ones(window) / window
    smoothed = np.convolve(medians, kernel, mode="valid")
    return float(np.std(smoothed))
