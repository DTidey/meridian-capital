"""Packet 63: Layer 4 loads prices for held tickers outside the candidate list."""

import logging
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest
import yaml

import portfolio.db  # noqa: F401
import run_portfolio
from data.db import daily_prices
from portfolio.db import portfolio_positions
from portfolio.rebalance import generate_trades

_SCORE_DATE = "2026-11-09"
_NAV = 100_000.0
_CONFIG = {
    "portfolio": {
        "nav_usd": _NAV,
        "turnover_budget_pct": 0.30,
        "adv_lookback_days": 20,
        "transaction_costs": {"spread_hl_fraction": 0.05, "market_impact_coef": 0.10},
        "mvo": {"cov_lookback_days": 130},
        "beta_lookback_days": 60,
    }
}


@pytest.fixture(autouse=True)
def _no_fred_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)


def _candidates():
    return pd.DataFrame(
        [
            {"ticker": "AAA", "combined_score": 80.0, "direction": "LONG", "sector": "S"},
            {"ticker": "ZZZ", "combined_score": 20.0, "direction": "SHORT", "sector": "S"},
        ]
    )


def _target():
    return pd.DataFrame(
        [
            {"ticker": "AAA", "shares": 50, "direction": "LONG", "combined_score": 80.0},
            {"ticker": "ZZZ", "shares": -40, "direction": "SHORT", "combined_score": 20.0},
        ]
    ).assign(weight=[0.05, -0.04], sector="S", beta=1.0, current_price=100.0)


def _insert_prices(conn, ticker, close, days=25, volume=1e6):
    dates = pd.bdate_range(end=_SCORE_DATE, periods=days)
    conn.execute(
        daily_prices.insert(),
        [
            {
                "ticker": ticker,
                "date": d.strftime("%Y-%m-%d"),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "adj_close": close,
                "volume": volume,
            }
            for d in dates
        ],
    )
    conn.commit()


def _insert_position(conn, ticker, shares):
    conn.execute(
        portfolio_positions.insert().values(
            ticker=ticker, shares=shares, direction="LONG" if shares > 0 else "SHORT"
        )
    )
    conn.commit()


def _price_frame(close, n=25):
    return pd.DataFrame(
        {"close": [close] * n, "high": [close * 1.01] * n, "low": [close * 0.99] * n}
    ).assign(volume=1e6)


# ---------------------------------------------------------------------------
# _price_tickers
# ---------------------------------------------------------------------------


class TestPriceTickers:
    def test_held_non_candidate_added(self):
        current = pd.DataFrame({"ticker": ["VICI", "AAA", "CRL"], "shares": [107, 5, -7]})
        assert run_portfolio._price_tickers(_candidates(), current) == [
            "AAA",
            "ZZZ",
            "CRL",
            "VICI",
        ]

    def test_candidates_only_unchanged(self):
        empty = pd.DataFrame(columns=["ticker", "shares"])
        assert run_portfolio._price_tickers(_candidates(), empty) == ["AAA", "ZZZ"]
        held_candidates = pd.DataFrame({"ticker": ["ZZZ", "AAA"], "shares": [-3, 5]})
        assert run_portfolio._price_tickers(_candidates(), held_candidates) == ["AAA", "ZZZ"]


# ---------------------------------------------------------------------------
# run() wiring
# ---------------------------------------------------------------------------


class _Spy:
    """Records the tickers each candidates-only consumer was given."""

    def __init__(self):
        self.calls = {}

    def betas(self, conn, tickers, score_date, lookback_days=60):
        self.calls["betas"] = list(tickers)
        return pd.Series(1.0, index=tickers)

    def events(self, tickers, score_date, conn, config):
        self.calls["events"] = list(tickers)
        return []

    def factors(self, conn, tickers, score_date):
        self.calls["factors"] = list(tickers)
        return pd.DataFrame()

    def optimise(self, candidates, prices, betas, config, score_date, conn):
        self.calls["optimiser"] = candidates["ticker"].tolist()
        return _target()


def _run_layer4(tmp_engine, tmp_path, monkeypatch):
    spy = _Spy()
    trades_out = {}
    real_generate = run_portfolio.generate_trades

    def _capture(current, target, prices, config, conn, score_date, commit=True):
        trades_out["prices"] = prices
        trades_out["trades"] = real_generate(
            current, target, prices, config, conn, score_date, commit=commit
        )
        return trades_out["trades"]

    monkeypatch.setattr(run_portfolio, "database_url", lambda: "sqlite://")
    monkeypatch.setattr(run_portfolio, "get_engine", lambda url: tmp_engine)
    monkeypatch.setattr(run_portfolio, "_load_candidates", lambda conn, d: _candidates())
    monkeypatch.setattr(run_portfolio, "compute_betas", spy.betas)
    monkeypatch.setattr(run_portfolio, "check_events", spy.events)
    monkeypatch.setattr(run_portfolio, "_load_factor_scores", spy.factors)
    monkeypatch.setattr(run_portfolio, "compute_exposures", lambda t, f: {"spread": {}})
    monkeypatch.setattr(run_portfolio, "get_nav", lambda *a, **k: _NAV)
    monkeypatch.setattr(run_portfolio, "_tail_risk_config", lambda conn, d, cfg, cache: cfg)
    monkeypatch.setattr(run_portfolio, "tilt_opt", SimpleNamespace(optimise=spy.optimise))
    monkeypatch.setattr(run_portfolio, "generate_trades", _capture)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(_CONFIG))
    args = Namespace(
        config=str(cfg_path),
        date=_SCORE_DATE,
        verbose=False,
        current=False,
        whatif=True,
        optimize_method="conviction",
    )
    run_portfolio.run(args)
    return spy, trades_out


def _row(trades, ticker):
    return trades.set_index("ticker").loc[ticker]


@pytest.fixture
def held_book(tmp_engine):
    """Candidates AAA/ZZZ; book holds VICI long 107 and CRL short 7, both non-candidates."""
    with tmp_engine.connect() as conn:
        _insert_prices(conn, "AAA", 100.0)
        _insert_prices(conn, "ZZZ", 50.0)
        _insert_prices(conn, "VICI", 24.0)
        _insert_prices(conn, "CRL", 150.0)
        _insert_position(conn, "VICI", 107)
        _insert_position(conn, "CRL", -7)
    return tmp_engine


class TestRunWiring:
    def test_held_non_candidate_priced(self, held_book, tmp_path, monkeypatch):
        _, out = _run_layer4(held_book, tmp_path, monkeypatch)
        trades = out["trades"]
        assert _row(trades, "VICI")["price"] == pytest.approx(24.0)
        assert _row(trades, "CRL")["price"] == pytest.approx(150.0)
        assert _row(trades, "VICI")["action"] == "SELL"
        assert _row(trades, "CRL")["action"] == "COVER"

    def test_held_non_candidate_has_cost(self, held_book, tmp_path, monkeypatch, capsys):
        _, out = _run_layer4(held_book, tmp_path, monkeypatch)
        trades = out["trades"]
        assert _row(trades, "VICI")["estimated_cost_usd"] > 0
        assert _row(trades, "CRL")["estimated_cost_usd"] > 0
        printed = capsys.readouterr().out
        vici_line = next(line for line in printed.splitlines() if " VICI " in line)
        assert "24.00" in vici_line and "$     0.00" not in vici_line

    def test_optimiser_universe_unchanged(self, held_book, tmp_path, monkeypatch):
        spy, out = _run_layer4(held_book, tmp_path, monkeypatch)
        for consumer in ("betas", "events", "factors", "optimiser"):
            assert spy.calls[consumer] == ["AAA", "ZZZ"], consumer
        trades = out["trades"]
        assert _row(trades, "VICI")["target_shares"] == 0
        assert _row(trades, "CRL")["target_shares"] == 0

    def test_no_held_non_candidates_same_tickers(self, tmp_engine, tmp_path, monkeypatch):
        with tmp_engine.connect() as conn:
            _insert_prices(conn, "AAA", 100.0)
            _insert_prices(conn, "ZZZ", 50.0)
            _insert_prices(conn, "OTHER", 10.0)  # stored but neither candidate nor held
            _insert_position(conn, "AAA", 20)
        _, out = _run_layer4(tmp_engine, tmp_path, monkeypatch)
        assert set(out["prices"]) == {"AAA", "ZZZ"}  # OTHER not loaded
        assert sorted(out["trades"]["ticker"]) == ["AAA", "ZZZ"]
        assert (out["trades"]["price"] > 0).all()


# ---------------------------------------------------------------------------
# Turnover accounting
# ---------------------------------------------------------------------------


def _trades(current_rows, target_rows, prices, budget=0.30):
    config = {"portfolio": {**_CONFIG["portfolio"], "turnover_budget_pct": budget}}
    current = pd.DataFrame(current_rows, columns=["ticker", "shares"])
    target = pd.DataFrame(target_rows, columns=["ticker", "shares", "direction", "combined_score"])
    return generate_trades(current, target, prices, config, None, _SCORE_DATE, commit=False)


class TestTurnover:
    def test_held_closure_counts_in_trade_value_and_summary(self):
        prices = {"AAA": _price_frame(100.0), "VICI": _price_frame(24.0)}
        trades = _trades(
            [("AAA", 50), ("VICI", 107)],
            [("AAA", 50, "LONG", 80.0)],
            prices,
        )
        summary = trades.attrs["turnover"]
        assert summary["needed_usd"] == pytest.approx(107 * 24.0)
        assert summary["kept_usd"] == pytest.approx(107 * 24.0)
        assert summary["kept_count"] == 1

    def test_held_long_counts_in_build_up_gap(self):
        """Current long gross includes the held VICI: 50*100 + 107*24 = 7568."""
        prices = {"AAA": _price_frame(100.0), "VICI": _price_frame(24.0)}
        # Target long gross 100*100 = 10000, so the build-up gap is 10000 - 7568 = 2432.
        trades = _trades(
            [("AAA", 50), ("VICI", 107)],
            [("AAA", 100, "LONG", 80.0)],
            prices,
        )
        summary = trades.attrs["turnover"]
        assert summary["needed_usd"] == pytest.approx(50 * 100.0 + 107 * 24.0)
        # Only the AAA add moves with the gap; exempt up to the gap, not the full 5000.
        assert summary["allowance_usd"] == pytest.approx(10_000 - 7_568)


# ---------------------------------------------------------------------------
# Missing-price warning
# ---------------------------------------------------------------------------


def _warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "No price for" in r.getMessage()
    ]


class TestMissingPriceWarning:
    def test_warns_for_unpriced_ticker(self, caplog):
        caplog.set_level(logging.WARNING, logger="portfolio.rebalance")
        trades = _trades(
            [("AAA", 50), ("GONE", -7)],
            [("AAA", 50, "LONG", 80.0)],
            {"AAA": _price_frame(100.0)},
        )
        msgs = _warnings(caplog)
        assert len(msgs) == 1 and "GONE" in msgs[0] and "AAA" not in msgs[0]
        assert _row(trades, "GONE")["action"] == "COVER"  # still emitted, not dropped
        assert _row(trades, "GONE")["price"] == 0.0

    def test_no_warning_when_all_priced(self, caplog):
        caplog.set_level(logging.WARNING, logger="portfolio.rebalance")
        _trades(
            [("AAA", 50), ("VICI", 107)],
            [("AAA", 60, "LONG", 80.0)],
            {"AAA": _price_frame(100.0), "VICI": _price_frame(24.0)},
        )
        assert _warnings(caplog) == []

    def test_nan_and_zero_close_warned(self, caplog):
        caplog.set_level(logging.WARNING, logger="portfolio.rebalance")
        prices = {
            "AAA": _price_frame(100.0),
            "NANC": _price_frame(float("nan")),
            "ZERO": _price_frame(0.0),
            "EMPTY": pd.DataFrame(),
        }
        trades = _trades(
            [("NANC", 10), ("ZERO", 5), ("EMPTY", -3)],
            [("AAA", 10, "LONG", 80.0)],
            prices,
        )
        msgs = _warnings(caplog)
        assert len(msgs) == 1
        for ticker in ("EMPTY", "NANC", "ZERO"):
            assert ticker in msgs[0]
        assert "AAA" not in msgs[0]
        assert trades.attrs["turnover"]["needed_usd"] == pytest.approx(10 * 100.0)
