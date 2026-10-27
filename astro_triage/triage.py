"""
triage.py

Frame classification logic.
"""


def classify_frame(
    star_metrics,
    star_ratio,
    background_grad,
    temporal_drift,
    thresholds,
):
    """
    Classifies a single frame.

    Returns
    -------
    label : str
    """

    if star_metrics is None:
        return "cloud_severe"

    if star_metrics["median_ellipticity"] > thresholds["ellipticity"]:
        return "guiding_or_trailing"

    if star_metrics["median_fwhm"] > thresholds["fwhm"]:
        return "soft_or_defocused"

    if star_ratio < thresholds["star_ratio"]:
        return "cloud_moderate"

    if temporal_drift > thresholds["temporal_drift"]:
        return "thin_cirrus"

    if background_grad > thresholds["background_gradient"]:
        return "cloud_gradient"

    return "usable"
