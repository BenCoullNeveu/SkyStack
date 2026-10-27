import multiprocessing as mp


def run_map(func, iterable, mode="cpu", n_workers=None):
    if mode == "cpu":
        return [func(x) for x in iterable]

    if mode == "mp":
        with mp.Pool(processes=n_workers) as pool:
            return pool.map(func, iterable)

    raise ValueError(f"Unknown execution mode: {mode}")
