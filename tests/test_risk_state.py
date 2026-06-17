"""Tests for risk/risk_state.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json

import pytest

from risk.risk_state import (
    clear_halt,
    default_state,
    is_halted,
    load_risk_state,
    save_risk_state,
    set_halt,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultState:
    def test_default_state_keys(self):
        """default_state() returns a dict with all expected top-level keys."""
        state = default_state()
        expected_keys = {
            "as_of",
            "nav_usd",
            "daily_pnl_usd",
            "daily_pnl_pct",
            "weekly_pnl_pct",
            "peak_nav_usd",
            "drawdown_pct",
            "halted",
            "circuit_breaker_state",
            "tail_risk_state",
            "gross_exposure",
            "net_exposure",
            "net_beta",
            "factor_exposures",
            "risk_decomposition",
            "mctr_top5",
            "correlation_monitor",
            "alerts",
        }
        assert expected_keys.issubset(set(state.keys()))


class TestHaltLock:
    def test_halt_lock_lifecycle(self, tmp_path):
        """set_halt creates file, is_halted returns True, clear_halt deletes it,
        is_halted returns False."""
        assert not is_halted(tmp_path)
        set_halt(tmp_path)
        assert (tmp_path / "halt.lock").exists()
        assert is_halted(tmp_path)
        clear_halt(tmp_path)
        assert not (tmp_path / "halt.lock").exists()
        assert not is_halted(tmp_path)


class TestSaveLoadRoundtrip:
    def test_save_and_load_roundtrip(self, tmp_path):
        """save then load a mutated state returns the same dict."""
        state = default_state()
        state["nav_usd"] = 12_345_678.0
        state["daily_pnl_pct"] = -0.015
        state["circuit_breaker_state"] = "SIZE_DOWN"
        save_risk_state(state, tmp_path)
        loaded = load_risk_state(tmp_path)
        assert loaded["nav_usd"] == pytest.approx(12_345_678.0)
        assert loaded["daily_pnl_pct"] == pytest.approx(-0.015)
        assert loaded["circuit_breaker_state"] == "SIZE_DOWN"

    def test_load_missing_returns_default(self, tmp_path):
        """load_risk_state on a nonexistent cache_dir returns default_state."""
        missing_dir = tmp_path / "does_not_exist"
        state = load_risk_state(missing_dir)
        expected = default_state()
        # Both should share the same top-level keys and default values
        assert set(state.keys()) == set(expected.keys())
        assert state["halted"] is False
        assert state["nav_usd"] == pytest.approx(0.0)

    def test_save_syncs_halted_flag(self, tmp_path):
        """After set_halt, save_risk_state sets state['halted']=True in the saved file."""
        state = default_state()
        set_halt(tmp_path)
        save_risk_state(state, tmp_path)
        loaded = load_risk_state(tmp_path)
        assert loaded["halted"] is True
