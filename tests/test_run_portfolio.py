"""Tests for Layer 4 (run_portfolio.py) and the tail-risk gross scale (packet 58)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest
import sqlalchemy as sa

import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
import run_portfolio
from data.db import daily_prices
from portfolio.book_targets import book_targets, scale_book_targets
from portfolio.db import position_approvals
from portfolio.optimizer import optimise
from portfolio.rebalance import generate_trades
from risk.db import risk_events, risk_log
from risk.tail_risk import run_tail_risk

_SCORE_DATE = "2026-11-04"


@pytest.fixture(autouse=True)
def _no_fred_key(monkeypatch):
    """No live FRED calls from tests (run_portfolio loads .env on import)."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)


_PORTFOLIO = {
    "nav_usd": 1_000_000,
    "num_longs": 10,
    "num_shorts": 10,
    "gross_exposure": 1.50,
    "target_long_gross": 0.80,
    "target_short_gross": 0.70,
    "net_exposure_min": 0.00,
    "net_exposure_max": 0.10,
    "max_position_pct": 0.15,
    "min_position_pct": 0.005,
    "max_sector_pct": 1.0,
    "max_sector_net_pct": 1.0,
    "max_beta": 1.0,
    "adv_max_pct": 1.0,
    "adv_lookback_days": 20,
    "earnings_blackout_days": 5,
    "turnover_budget_pct": 10.0,  # these tests are about the gross scale, not the budget
    "conviction_tilt": {"top5_multiplier": 1.50, "top10_multiplier": 1.25},
}


def _config(**overrides):
    pcfg = dict(_PORTFOLIO)
    pcfg.update(overrides)
    return {
        "portfolio": pcfg,
        "risk": {
            "pre_trade": {
                "max_gross": 1.65,
                "net_min": -0.10,
                "net_max": 0.15,
                "max_position_pct": 0.15,  # as portfolio.max_position_pct above
            },
            "tail_risk": {
                "vix_caution": 25.0,
                "vix_stress": 35.0,
                "credit_spread_sigma": 2.0,
                "credit_lookback_days": 252,
            },
        },
    }


def _candidates():
    rows = [
        {"ticker": f"L{i:02d}", "direction": "LONG", "combined_score": 90.0 - i, "sector": "S"}
        for i in range(10)
    ]
    rows += [
        {"ticker": f"S{i:02d}", "direction": "SHORT", "combined_score": 10.0 + i, "sector": "S"}
        for i in range(10)
    ]
    return pd.DataFrame(rows)


def _prices(candidates):
    df = pd.DataFrame(
        {"close": [100.0] * 25, "high": [101.0] * 25, "low": [99.0] * 25, "volume": [1e6] * 25}
    )
    return dict.fromkeys(candidates["ticker"], df)


def _target(conn, config):
    cands = _candidates()
    betas = pd.Series(1.0, index=cands["ticker"])
    return optimise(cands, _prices(cands), betas, config, _SCORE_DATE, conn)


def _insert_vix(conn, close):
    conn.execute(
        daily_prices.insert().values(
            ticker="^VIX", date=_SCORE_DATE, open=close, high=close, low=close, close=close
        )
    )
    conn.commit()


def _counts(conn):
    return tuple(
        conn.execute(sa.select(sa.func.count()).select_from(t)).scalar()
        for t in (position_approvals, risk_events, risk_log)
    )


class TestTailRiskGrossScale:
    def test_normal_full_gross(self, tmp_db):
        scaled = scale_book_targets(_config(), 1.0)
        assert book_targets(scaled) == pytest.approx((0.80, 0.70))
        w = _target(tmp_db, scaled)["weight"]
        assert w.abs().sum() == pytest.approx(1.50, abs=0.01)

    def test_caution_scales_book(self, tmp_db):
        scaled = scale_book_targets(_config(), 0.8)
        assert book_targets(scaled) == pytest.approx((0.64, 0.56))
        target = _target(tmp_db, scaled)
        longs = target[target["direction"] == "LONG"]["weight"].abs().sum()
        shorts = target[target["direction"] == "SHORT"]["weight"].abs().sum()
        assert (longs, shorts) == (pytest.approx(0.64, abs=0.01), pytest.approx(0.56, abs=0.01))

    def test_stress_scales_book(self, tmp_db):
        scaled = scale_book_targets(_config(), 0.5)
        assert book_targets(scaled) == pytest.approx((0.40, 0.35))
        assert _target(tmp_db, scaled)["weight"].abs().sum() == pytest.approx(0.75, abs=0.01)

    def test_input_config_not_mutated(self):
        cfg = _config()
        scale_book_targets(cfg, 0.8)
        assert cfg["portfolio"]["target_long_gross"] == 0.80

    def test_scaled_book_still_validated(self):
        # Adversarial: STRESS takes net 0.10 to 0.05, below a 0.06 floor -> refuse, do not build.
        with pytest.raises(ValueError, match="net_exposure_min"):
            scale_book_targets(_config(net_exposure_min=0.06), 0.5)

    @pytest.mark.parametrize("scale", [0.0, -0.2, 1.2])
    def test_scale_out_of_range_refused(self, scale):
        with pytest.raises(ValueError, match="scale"):
            scale_book_targets(_config(), scale)

    def test_layer4_uses_regime(self, tmp_db, tmp_path, monkeypatch, capsys):
        regime = {
            "tail_risk_state": "CAUTION",
            "action": "REDUCE_GROSS_20",
            "reduction_pct": 0.20,
            "vix": 18.0,
            "credit_spread_z": 2.57,
        }
        monkeypatch.setattr(run_portfolio, "evaluate_tail_risk", lambda *a, **k: regime)
        scaled = run_portfolio._tail_risk_config(tmp_db, _SCORE_DATE, _config(), tmp_path)
        assert book_targets(scaled) == pytest.approx((0.64, 0.56))
        out = capsys.readouterr().out
        assert "CAUTION" in out and "120%" in out

    def test_whatif_no_writes(self, tmp_db, tmp_path):
        _insert_vix(tmp_db, 38.0)
        before = _counts(tmp_db)
        scaled = run_portfolio._tail_risk_config(tmp_db, _SCORE_DATE, _config(), tmp_path)
        assert book_targets(scaled) == pytest.approx((0.40, 0.35))
        assert _counts(tmp_db) == before

    def test_second_night_no_churn(self, tmp_db, tmp_path):
        """Night 1 under CAUTION fills; night 2 on the same inputs trades nothing."""
        _insert_vix(tmp_db, 28.0)
        config = run_portfolio._tail_risk_config(tmp_db, _SCORE_DATE, _config(), tmp_path)
        target = _target(tmp_db, config)
        prices = _prices(_candidates())
        generate_trades(pd.DataFrame(), target, prices, config, tmp_db, _SCORE_DATE)
        tmp_db.execute(position_approvals.update().values(status="APPROVED"))
        tmp_db.commit()
        run_tail_risk(tmp_db, _SCORE_DATE, config, tmp_path, whatif=False)

        filled = tmp_db.execute(
            sa.select(position_approvals.c.ticker, position_approvals.c.target_shares)
        ).fetchall()
        book = pd.DataFrame(filled, columns=["ticker", "shares"])
        night2 = generate_trades(book, target, prices, config, tmp_db, _SCORE_DATE, commit=False)
        assert (night2["action"] == "HOLD").all()


class TestTurnoverSummary:
    def test_turnover_summary_printed(self, capsys):
        """Packet 57 AC7: needed, allowance, rotation budget, kept and trimmed."""
        rows = [
            {
                "ticker": f"L{i}",
                "shares": 1000 + (500 if i % 2 == 0 else -500),
                "direction": "LONG",
                "combined_score": 60.0 + i,
            }
            for i in range(10)
        ]
        current = pd.DataFrame([(f"L{i}", 1000) for i in range(10)], columns=["ticker", "shares"])
        target = pd.DataFrame(rows)
        cands = pd.DataFrame({"ticker": target["ticker"]})
        trades = generate_trades(
            current,
            target,
            _prices(cands),
            _config(turnover_budget_pct=0.20),
            None,
            _SCORE_DATE,
            commit=False,
        )
        run_portfolio._print_turnover_summary(trades)
        out = capsys.readouterr().out
        assert "Turnover" in out
        assert "needed $500,000 (50.0% NAV)" in out
        assert "build-up allowance $0 (0.0%)" in out
        assert "rotation budget $200,000 (20.0%)" in out
        trimmed = (trades["action"] == "HOLD").sum()
        assert f"trimmed {trimmed}" in out

    def test_summary_missing_is_silent(self, capsys):
        run_portfolio._print_turnover_summary(pd.DataFrame())
        assert capsys.readouterr().out == ""
