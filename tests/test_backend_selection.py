"""Tests for skystack.backends.select_backend()."""
from __future__ import annotations

import pytest

from skystack.backends import select_backend
from skystack.backends.pixinsight import PixInsightBackend


def test_select_backend_defaults_to_pixinsight():
    backend = select_backend({})
    assert isinstance(backend, PixInsightBackend)


def test_select_backend_is_case_insensitive():
    backend = select_backend({"processing_backend": "PixInsight"})
    assert isinstance(backend, PixInsightBackend)


def test_select_backend_unknown_name_raises_clear_error():
    with pytest.raises(ValueError, match="unknown_backend"):
        select_backend({"processing_backend": "unknown_backend"})
