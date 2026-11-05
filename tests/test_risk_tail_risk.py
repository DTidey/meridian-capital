"""Tests for risk/tail_risk.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from portfolio.db import position_approvals
from risk.tail_risk import run_tail_risk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_DATE = "2026-03-15"
_VIX_TICKER = "^VIX"


@pytest.fixture(autouse=True)
def _no_fred_key(monkeypatch):
    """No live FRED calls from tests ; tests that need a key set a fake one."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)


@pytest.fixture
def mem_engine():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def mem_db(mem_engine):
    conn = mem_engine.connect()
    yield conn
    conn.close()


def _base_config(vix_caution=25.0, vix_stress=35.0):
    return {
        "risk": {
            "tail_risk": {
                "vix_caution": vix_caution,
                "vix_stress": vix_stress,
                "credit_spread_sigma": 99.0,  # disabled for these tests
                "credit_lookback_days": 10,
            }
        }
    }


def _insert_vix(conn, vix_close, score_date=_SCORE_DATE):
    """Insert a single VIX price row."""
    conn.execute(
        daily_prices.insert().values(
            ticker=_VIX_TICKER,
            date=score_date,
            open=vix_close,
            high=vix_close,
            low=vix_close,
            close=vix_close,
            adj_close=vix_close,
            volume=0,
        )
    )
    conn.commit()


def _insert_approval(conn, ticker, action="BUY", target=300.0, current=0.0):
    conn.execute(
        position_approvals.insert().values(
            rebalance_date=_SCORE_DATE,
            ticker=ticker,
            action=action,
            target_shares=target,
            current_shares=current,
            delta_shares=target - current,
            status="APPROVED",
            created_at="2026-03-15T00:00:00",
        )
    )
    conn.commit()


def _approval(conn, ticker):
    return conn.execute(
        sa.select(position_approvals).where(position_approvals.c.ticker == ticker)
    ).fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTailRiskNormal:
    def test_vix_below_threshold_normal(self, mem_db, tmp_path):
        """VIX=20, no action, state='NORMAL'."""
        _insert_vix(mem_db, 20.0)
        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert result["tail_risk_state"] == "NORMAL"
        assert result["actions"] == []


class TestTailRiskCaution:
    def test_vix_caution_flags_20pct(self, mem_db, tmp_path):
        """VIX=28: REDUCE_GROSS_20 reported; packet 58: the approval is not edited."""
        _insert_vix(mem_db, 28.0)
        _insert_approval(mem_db, "AAPL", action="BUY", target=500)

        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)
        assert result["tail_risk_state"] == "CAUTION"
        assert "REDUCE_GROSS_20" in result["actions"]
        assert _approval(mem_db, "AAPL").target_shares == pytest.approx(500.0)


class TestTailRiskStress:
    def test_vix_stress_flags_50pct(self, mem_db, tmp_path):
        """VIX=38: REDUCE_GROSS_50 reported; packet 58: the approval is not edited."""
        _insert_vix(mem_db, 38.0)
        _insert_approval(mem_db, "MSFT", action="BUY", target=200)

        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)
        assert result["tail_risk_state"] == "STRESS"
        assert "REDUCE_GROSS_50" in result["actions"]
        assert _approval(mem_db, "MSFT").target_shares == pytest.approx(200.0)


class TestTailRiskClosingTrades:
    def test_closing_trades_not_reduced(self, mem_db, tmp_path):
        """SELL trades are never resized regardless of VIX."""
        _insert_vix(mem_db, 38.0)
        # Insert a SELL (closing) trade
        _insert_approval(mem_db, "TSLA", action="SELL", target=0.0, current=300.0)

        run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)

        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "TSLA"
            )
        ).fetchone()
        assert row is not None
        # SELL is a closing trade; target_shares=0 should be untouched
        assert row[0] == pytest.approx(0.0, abs=0.01)


class TestTailRiskWhatIf:
    def test_whatif_does_not_modify_db(self, mem_db, tmp_path):
        """VIX=38 but whatif=True, DB unchanged."""
        _insert_vix(mem_db, 38.0)
        _insert_approval(mem_db, "NVDA", action="BUY", target=400)

        run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)

        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "NVDA"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(400.0)  # unchanged


class TestTailRiskMissingVIX:
    def test_missing_vix_returns_normal(self, mem_db, tmp_path):
        """No ^VIX data → state='NORMAL', no action."""
        # Do not insert any VIX price
        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert result["tail_risk_state"] == "NORMAL"
        assert result["vix"] == pytest.approx(0.0)
        assert result["actions"] == []


# ---------------------------------------------------------------------------
# Packet 59: credit-spread lookback (FRED) and threshold
# ---------------------------------------------------------------------------

_FRED_KEY = "test-fred-key-do-not-log"
_CREDIT_SCORE_DATE = "2026-11-04"


def _bdays(n, end=_CREDIT_SCORE_DATE):
    import pandas as pd

    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=end, periods=n)]


def _fake_fred(monkeypatch, observations, captured=None):
    """Patch requests.get; honours observation_start like the FRED API."""
    import requests

    class _Resp:
        def __init__(self, obs):
            self._obs = obs

        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": self._obs}

    def _get(url, params=None, timeout=None):
        if captured is not None:
            captured.update(params or {})
        start = (params or {}).get("observation_start", "0000-00-00")
        return _Resp([o for o in observations if o["date"] >= start])

    monkeypatch.setattr(requests, "get", _get)


def _obs(dates, values):
    return [
        {"date": d, "value": v if isinstance(v, str) else f"{v:.4f}"}
        for d, v in zip(dates, values, strict=True)
    ]


def _z(values):
    import numpy as np

    arr = np.asarray(values, dtype=float)
    return (arr[-1] - arr.mean()) / arr.std(ddof=1)


def _credit_cfg(lookback=252, sigma=2.0):
    return {"credit_lookback_days": lookback, "credit_spread_sigma": sigma}


def _series(n):
    """Deterministic, non-flat series: early points far from later ones."""
    return [5.0 + (i % 7) * 0.1 if i < n - 252 else 3.0 + (i % 11) * 0.05 for i in range(n)]


class TestCreditLookback:
    def test_uses_exact_lookback_obs(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        dates, vals = _bdays(300), _series(300)
        _fake_fred(monkeypatch, _obs(dates, vals))
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals[-252:]))

    def test_observation_start_covers_business_days(self, monkeypatch, tmp_path):
        from datetime import date

        from risk.tail_risk import _get_credit_spread_z_fred

        captured: dict = {}
        _fake_fred(monkeypatch, _obs(_bdays(300), _series(300)), captured)
        _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        start = date.fromisoformat(captured["observation_start"])
        assert (date.fromisoformat(_CREDIT_SCORE_DATE) - start).days >= 353 + 30

    def test_short_window_warns_and_computes(self, monkeypatch, tmp_path, caplog):
        from risk.tail_risk import _get_credit_spread_z_fred

        vals = [3.0 + (i % 9) * 0.03 for i in range(200)]
        _fake_fred(monkeypatch, _obs(_bdays(200), vals))
        with caplog.at_level("WARNING", logger="risk.tail_risk"):
            z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals))
        assert any(r.levelname == "WARNING" and "200" in r.getMessage() for r in caplog.records)

    def test_missing_values_not_counted(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        dates, vals = _bdays(330), _series(330)
        # Every 10th observation in the last stretch is missing (".").
        raw = [v if (i < 200 or i % 10) else "." for i, v in enumerate(vals)]
        _fake_fred(monkeypatch, _obs(dates, raw))
        numeric = [v for v in raw if not isinstance(v, str)]
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(numeric[-252:]))

    def test_info_line_without_api_key(self, monkeypatch, tmp_path, caplog):
        from risk.tail_risk import _get_credit_spread_z_fred

        _fake_fred(monkeypatch, _obs(_bdays(300), _series(300)))
        with caplog.at_level("DEBUG", logger="risk.tail_risk"):
            _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        info = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any("obs=252" in m and "mean=" in m and "std=" in m and "z=" in m for m in info)
        assert all(_FRED_KEY not in r.getMessage() for r in caplog.records)

    # Edge and adversarial cases
    def test_weekend_score_date(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        dates, vals = _bdays(300, end="2026-10-30"), _series(300)  # Friday
        _fake_fred(monkeypatch, _obs(dates, vals))
        z = _get_credit_spread_z_fred("2026-11-01", _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals[-252:]))

    def test_latest_missing_uses_previous(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        dates, vals = _bdays(301), _series(301)
        raw = vals[:-1] + ["."]
        _fake_fred(monkeypatch, _obs(dates, raw))
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals[:-1][-252:]))

    def test_flat_series_zero(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        _fake_fred(monkeypatch, _obs(_bdays(300), [3.0] * 300))
        assert (
            _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path) == 0.0
        )

    def test_future_observations_excluded(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        dates, vals = _bdays(300), _series(300)
        future = _obs(["2026-11-05", "2026-11-06"], [9.0, 9.5])
        _fake_fred(monkeypatch, _obs(dates, vals) + future)
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals[-252:]))

    def test_unsorted_response_sorted(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        dates, vals = _bdays(300), _series(300)
        _fake_fred(monkeypatch, list(reversed(_obs(dates, vals))))
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals[-252:]))

    def test_minimum_ten_boundary(self, monkeypatch, tmp_path):
        from risk.tail_risk import _get_credit_spread_z_fred

        vals = [3.0, 3.1, 3.05, 3.2, 3.15, 3.0, 3.1, 3.25, 3.3, 3.4]
        _fake_fred(monkeypatch, _obs(_bdays(10), vals))
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)
        assert z == pytest.approx(_z(vals))
        _fake_fred(monkeypatch, _obs(_bdays(9), vals[1:]))
        with pytest.raises(ValueError, match="Insufficient"):
            _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(), tmp_path)

    def test_lookback_from_config(self, monkeypatch, tmp_path):
        from datetime import date

        from risk.tail_risk import _get_credit_spread_z_fred

        captured: dict = {}
        dates, vals = _bdays(300), _series(300)
        _fake_fred(monkeypatch, _obs(dates, vals), captured)
        z = _get_credit_spread_z_fred(_CREDIT_SCORE_DATE, _FRED_KEY, _credit_cfg(60), tmp_path)
        assert z == pytest.approx(_z(vals[-60:]))
        start = date.fromisoformat(captured["observation_start"])
        assert (date.fromisoformat(_CREDIT_SCORE_DATE) - start).days >= 84 + 30


class TestCreditThreshold:
    def _run(self, mem_db, tmp_path, monkeypatch, z, sigma):
        import risk.tail_risk as tr

        monkeypatch.setattr(tr, "_get_credit_spread_z", lambda *a, **k: z)
        cfg = _base_config()
        cfg["risk"]["tail_risk"]["credit_spread_sigma"] = sigma
        _insert_vix(mem_db, 18.0)
        return run_tail_risk(mem_db, _SCORE_DATE, cfg, tmp_path, whatif=True)

    def test_credit_threshold_boundary(self, mem_db, tmp_path, monkeypatch):
        assert self._run(mem_db, tmp_path, monkeypatch, 2.0, 2.0)["tail_risk_state"] == "CAUTION"

    def test_just_below_threshold_normal(self, mem_db, tmp_path, monkeypatch):
        assert self._run(mem_db, tmp_path, monkeypatch, 1.99, 2.0)["tail_risk_state"] == "NORMAL"

    def test_config_sigma_is_two(self):
        import yaml

        cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
        assert cfg["risk"]["tail_risk"]["credit_spread_sigma"] == 2.0


# ---------------------------------------------------------------------------
# Packet 58: regime evaluated read-only; Layer 5 never edits approvals
# ---------------------------------------------------------------------------


def _row_counts(conn):
    from risk.db import risk_events, risk_log

    return tuple(
        conn.execute(sa.select(sa.func.count()).select_from(t)).scalar()
        for t in (position_approvals, risk_events, risk_log)
    )


def _approval_tuple(conn, ticker):
    row = _approval(conn, ticker)
    return (row.action, row.target_shares, row.delta_shares, row.status)


class TestEvaluateTailRisk:
    @pytest.mark.parametrize(
        ("vix", "credit_z", "state", "action", "reduction"),
        [
            (20.0, 0.0, "NORMAL", None, 0.0),
            (28.0, 0.0, "CAUTION", "REDUCE_GROSS_20", 0.20),
            (18.0, 2.5, "CAUTION", "REDUCE_GROSS_20", 0.20),
            (38.0, 2.5, "STRESS", "REDUCE_GROSS_50", 0.50),
        ],
    )
    def test_matches_run_tail_risk(
        self, mem_db, tmp_path, monkeypatch, vix, credit_z, state, action, reduction
    ):
        import risk.tail_risk as tr

        monkeypatch.setattr(tr, "_get_credit_spread_z", lambda *a, **k: credit_z)
        _insert_vix(mem_db, vix)
        cfg = _base_config()
        cfg["risk"]["tail_risk"]["credit_spread_sigma"] = 2.0
        regime = tr.evaluate_tail_risk(mem_db, _SCORE_DATE, cfg, tmp_path)
        assert (regime["tail_risk_state"], regime["action"]) == (state, action)
        assert regime["reduction_pct"] == pytest.approx(reduction)
        assert regime["vix"] == pytest.approx(vix)
        assert regime["credit_spread_z"] == pytest.approx(credit_z)
        full = tr.run_tail_risk(mem_db, _SCORE_DATE, cfg, tmp_path, whatif=True)
        assert full["tail_risk_state"] == regime["tail_risk_state"]

    def test_writes_nothing(self, mem_db, tmp_path):
        from risk.tail_risk import evaluate_tail_risk

        _insert_vix(mem_db, 38.0)
        _insert_approval(mem_db, "NVDA", action="BUY", target=400)
        before = _row_counts(mem_db)
        evaluate_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path)
        assert _row_counts(mem_db) == before
        assert _approval(mem_db, "NVDA").target_shares == pytest.approx(400.0)

    def test_fred_failure_falls_back(self, mem_db, tmp_path, monkeypatch):
        # Adversarial: FRED raises; both entry points fall back to HYG the same way.
        import requests

        from risk.tail_risk import evaluate_tail_risk

        def _boom(*a, **k):
            raise requests.ConnectionError("down")

        monkeypatch.setenv("FRED_API_KEY", _FRED_KEY)
        monkeypatch.setattr(requests, "get", _boom)
        _insert_vix(mem_db, 18.0)
        regime = evaluate_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path)
        full = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert regime["tail_risk_state"] == full["tail_risk_state"] == "NORMAL"
        assert regime["credit_spread_z"] == pytest.approx(0.0)

    def test_api_key_not_logged(self, mem_db, tmp_path, monkeypatch, caplog):
        from risk.tail_risk import evaluate_tail_risk

        monkeypatch.setenv("FRED_API_KEY", _FRED_KEY)
        _fake_fred(monkeypatch, _obs(_bdays(300, end=_SCORE_DATE), _series(300)))
        _insert_vix(mem_db, 18.0)
        with caplog.at_level("DEBUG"):
            evaluate_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path)
        assert caplog.records
        assert all(_FRED_KEY not in r.getMessage() for r in caplog.records)


class TestNoApprovalEdits:
    @pytest.mark.parametrize("vix", [28.0, 38.0])
    def test_regime_leaves_rows(self, mem_db, tmp_path, vix):
        from risk.db import risk_events

        _insert_vix(mem_db, vix)
        _insert_approval(mem_db, "NKE", action="SHORT", target=-50.0, current=-41.0)
        _insert_approval(mem_db, "KIM", action="BUY", target=192.0, current=0.0)
        before = {t: _approval_tuple(mem_db, t) for t in ("NKE", "KIM")}
        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)
        assert result["tail_risk_state"] in ("CAUTION", "STRESS")
        assert {t: _approval_tuple(mem_db, t) for t in ("NKE", "KIM")} == before
        events = mem_db.execute(sa.select(risk_events)).fetchall()
        assert len(events) == 1  # the regime is still recorded

    def test_small_delta_untouched(self, mem_db, tmp_path):
        # Adversarial: a 1-share add must not become a 0-share or reversed row.
        _insert_vix(mem_db, 28.0)
        _insert_approval(mem_db, "MU", action="BUY", target=4.0, current=3.0)
        run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)
        assert _approval_tuple(mem_db, "MU") == ("BUY", 4.0, 1.0, "APPROVED")

    def test_layer5_regime_differs_no_edit(self, mem_db, tmp_path, monkeypatch):
        # Adversarial: Layer 4 saw NORMAL, Layer 5 sees CAUTION; Layer 5 still edits nothing.
        import risk.tail_risk as tr

        monkeypatch.setattr(tr, "_get_credit_spread_z", lambda *a, **k: 3.0)
        cfg = _base_config()
        cfg["risk"]["tail_risk"]["credit_spread_sigma"] = 2.0
        _insert_vix(mem_db, 18.0)
        _insert_approval(mem_db, "AVY", action="BUY", target=24.0, current=0.0)
        result = run_tail_risk(mem_db, _SCORE_DATE, cfg, tmp_path, whatif=False)
        assert result["tail_risk_state"] == "CAUTION"
        assert _approval_tuple(mem_db, "AVY") == ("BUY", 24.0, 24.0, "APPROVED")
