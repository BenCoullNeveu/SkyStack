"""
backend.py

Numerical backend selection and capability queries.
"""

import numpy as np

try:
    import cupy as cp
    CUPY_AVAILABLE = True
except ImportError:
    CUPY_AVAILABLE = False


def select_backend(config_backend: str):
    if config_backend == "cpu":
        return "cpu"

    if config_backend == "mp":
        return "mp"

    if config_backend == "gpu":
        if not CUPY_AVAILABLE:
            raise RuntimeError("GPU backend requested but CuPy not available")
        return "gpu"

    # auto
    if CUPY_AVAILABLE:
        try:
            _ = cp.zeros((1,))
            return "gpu"
        except Exception:
            pass

    return "mp"


def xp(backend: str):
    if backend == "gpu":
        return cp
    return np


def has_gpu_fft(backend: str) -> bool:
    return backend == "gpu"


def asnumpy(arr):
    if CUPY_AVAILABLE and isinstance(arr, cp.ndarray):
        return cp.asnumpy(arr)
    return arr