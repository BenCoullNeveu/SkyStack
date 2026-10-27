"""Tests for the FakeBackend test double itself (not the protocol contract
-- see tests/test_backend_contract.py for that)."""
from __future__ import annotations

from tests.backends.fake_backend import FakeBackend


def test_start_can_be_called_more_than_once():
    b = FakeBackend()
    b.start()
    b.start()
    assert b.start_calls == 2
    assert b.started is True


def test_stop_marks_not_started():
    b = FakeBackend()
    b.start()
    b.stop()
    assert b.started is False
    assert b.stop_calls == 1
