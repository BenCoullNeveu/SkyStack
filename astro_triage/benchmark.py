"""
benchmark.py

Simple performance benchmarking helpers.
"""

import time


def timeit(func, *args, label=None, **kwargs):
    t0 = time.perf_counter()
    result = func(*args, **kwargs)
    dt = time.perf_counter() - t0

    name = label or func.__name__
    print(f"[BENCH] {name}: {dt:.3f} s")

    return result, dt