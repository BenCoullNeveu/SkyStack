"""Shared fixtures for the SkyStack test suite.

Provides factories for tiny synthetic FITS/XISF frames so tests don't need
real telescope data or any external application installed.
"""
from __future__ import annotations

import itertools
from pathlib import Path
from typing import Optional

import numpy as np
import pytest
from astropy.io import fits

from astro_utils.config_loader import load_config
from skystack.domain import FrameKeywordMap

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def repo_keymap() -> FrameKeywordMap:
    """The keyword map matching this repo's actual config.yaml.

    Used for parity tests against astro_utils.fits_helpers, whose
    module-level HEADER_* constants are loaded from this same file at
    import time.
    """
    config = load_config(REPO_ROOT / "config.yaml")
    return FrameKeywordMap.from_config(config)


@pytest.fixture
def synthetic_fits_file(tmp_path):
    """Factory: build a small FITS file with the given header keywords."""
    counter = itertools.count()

    def _make(data: Optional[np.ndarray] = None, **header_kwargs) -> Path:
        if data is None:
            data = np.zeros((8, 8), dtype=np.float32)
        hdr = fits.Header()
        for key, value in header_kwargs.items():
            hdr[key] = value
        path = tmp_path / f"frame_{next(counter)}.fits"
        fits.PrimaryHDU(data=data, header=hdr).writeto(path, overwrite=True)
        return path

    return _make


@pytest.fixture
def synthetic_xisf_file(tmp_path):
    """Factory: build a small XISF file with the given FITSKeywords."""
    from xisf import XISF

    counter = itertools.count()

    def _make(data: Optional[np.ndarray] = None, **header_kwargs) -> Path:
        if data is None:
            data = np.zeros((8, 8, 1), dtype=np.float32)
        fits_keywords = {
            key: [{"value": str(value), "comment": ""}]
            for key, value in header_kwargs.items()
        }
        path = tmp_path / f"frame_{next(counter)}.xisf"
        XISF.write(
            str(path),
            data,
            creator_app="skystack-tests",
            image_metadata={"FITSKeywords": fits_keywords},
            xisf_metadata={},
            codec=None,
            shuffle=True,
        )
        return path

    return _make


@pytest.fixture
def pixinsight_control_dir():
    """Ensures the fixed IPC directory astro_utils/pixinsight_cli.py uses
    exists. Tests only touch files they themselves create inside it, never
    the directory's other contents, and rely on the production code's own
    sentinel-cleanup to remove what they create.
    """
    control_dir = Path("C:/Temp/PixStack")
    control_dir.mkdir(parents=True, exist_ok=True)
    return control_dir


@pytest.fixture(autouse=True)
def isolated_pipeline_db(tmp_path, monkeypatch):
    """Keep pipeline stages (frame metrics) away from the real skystack.db."""
    from skystack import pipeline

    db_path = tmp_path / "skystack_test.db"
    monkeypatch.setattr(pipeline, "DB_PATH", db_path)
    return db_path


@pytest.fixture(autouse=True)
def isolated_web_triage_registry(tmp_path, monkeypatch):
    """Keep web triage sessions away from the real pipeline_jobs/web_triage/."""
    from skystack import triage_server

    reg = tmp_path / "web_triage"
    monkeypatch.setattr(triage_server, "REGISTRY_DIR", reg)
    return reg
