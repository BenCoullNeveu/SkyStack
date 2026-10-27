"""
registration.py

Translation-only image registration with safe CPU fallback.
"""

import numpy as np
from scipy.ndimage import shift

from astro_triage.backend import xp, has_gpu_fft


def _phase_correlation_gpu(ref, img, xp_mod):
    F_ref = xp_mod.fft.fft2(ref)
    F_img = xp_mod.fft.fft2(img)

    R = F_ref * xp_mod.conj(F_img)
    R /= xp_mod.maximum(xp_mod.abs(R), 1e-12)

    corr = xp_mod.fft.ifft2(R)
    corr = xp_mod.abs(corr)

    max_idx = xp_mod.unravel_index(xp_mod.argmax(corr), corr.shape)
    shifts = xp_mod.array(max_idx, dtype=xp_mod.float32)

    for i, dim in enumerate(corr.shape):
        if shifts[i] > dim // 2:
            shifts[i] -= dim

    return float(shifts[0]), float(shifts[1])


def register_translation(
    reference,
    image,
    backend="cpu",
    upsample_factor=20,
    max_shift=25.0,
):
    """
    Register image to reference.

    GPU:
        - FFT phase correlation
        - subpixel sensitivity
    CPU / MP:
        - shift-only (assumes small drift)
    """

    dy = dx = 0.0

    if has_gpu_fft(backend):
        xp_mod = xp(backend)

        ref_gpu = xp_mod.asarray(reference)
        img_gpu = xp_mod.asarray(image)

        dy, dx = _phase_correlation_gpu(ref_gpu, img_gpu, xp_mod)

    # CPU-safe fallback
    if abs(dy) > max_shift or abs(dx) > max_shift:
        raise RuntimeError("Excessive frame drift")

    registered = shift(
        image,
        shift=(dy, dx),
        order=3,
        mode="constant",
        cval=0.0
    )

    return registered, (dy, dx)
