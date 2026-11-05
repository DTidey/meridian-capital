"""Packet 61: a short whose target drops to 0 is covered in full (Layer 4 through Layer 6)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unittest.mock import MagicMock

import pandas as pd
import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices
from execution.executor import execute_approvals
from portfolio.db import position_approvals
from portfolio.rebalance import generate_trades, map_action
from risk.pre_trade import run_pre_trade
from risk.risk_state import set_halt

_DATE = "2026-11-05"
_CONFIG = {
    "portfolio": {
        "nav_usd": 1_000_000,
        "turnover_budget_pct": 0.20,
        "transaction_costs": {"spread_hl_fraction": 0.05, "market_impact_coef": 0.10},
        "adv_lookback_days": 5,
    },
    "execution": {"limit_slippage_pct": 0.015, "max_adv_pct": 0.02, "shortable_cache_days": 7},
}


def _px(close=100.0):
    return pd.DataFrame({"close": [close] * 10, "high": [close + 1] * 10, "low": [close - 1] * 10,
                         "volume": [10_000.0] * 10})  # fmt: skip


def _cur(rows):
    return pd.DataFrame(rows, columns=["ticker", "shares"])


def _tgt(rows):
    base = {"direction": "LONG", "combined_score": 75.0}
    return pd.DataFrame([{**base, **r} for r in rows], columns=["ticker", "shares", *base])


def _trades(current, target, prices=None, conn=None, commit=False, config=_CONFIG):
    tickers = set(current["ticker"]) | set(target["ticker"])
    prices = {t: _px() for t in tickers} if prices is None else prices
    return generate_trades(current, target, prices, config, conn, _DATE, commit=commit)


def _row(trades, ticker):
    return trades.set_index("ticker").loc[ticker]


# ---------------------------------------------------------------------------
# AC1-AC3: action mapping
# ---------------------------------------------------------------------------


class TestMapAction:
    def test_short_to_zero_is_cover(self):  # AC1
        assert map_action(-10, 0, 10) == "COVER"

    def test_long_to_zero_is_sell(self):  # AC2
        assert map_action(10, 0, -10) == "SELL"

    @pytest.mark.parametrize(
        ("cur", "tgt", "action"),
        [
            (0, 10, "BUY"),  # open long
            (10, 15, "BUY"),  # add long
            (10, 5, "SELL"),  # reduce long
            (0, -10, "SHORT"),  # open short
            (-10, -15, "SHORT"),  # add short
            (-10, -5, "COVER"),  # reduce short
            (-10, 5, "BUY"),  # short to long
            (10, -5, "SHORT"),  # long to short
            (10, 10, "HOLD"),
            (-10, -10, "HOLD"),
            (0, 0, "HOLD"),
        ],
    )
    def test_other_cases_unchanged(self, cur, tgt, action):  # AC3
        assert map_action(cur, tgt, tgt - cur) == action


class TestGenerateTrades:
    def test_short_absent_from_target_covered_in_full(self, tmp_db):  # AC1
        row = _row(_trades(_cur([("S", -37)]), _tgt([])), "S")
        assert (row["action"], row["target_shares"], row["delta_shares"]) == ("COVER", 0, 37)
        assert row["direction"] == "SHORT"

    def test_short_with_zero_target_row_covered(self, tmp_db):  # AC1
        target = _tgt([{"ticker": "S", "shares": 0.0, "direction": "SHORT"}])
        row = _row(_trades(_cur([("S", -37)]), target), "S")
        assert (row["action"], row["delta_shares"]) == ("COVER", 37)

    def test_long_absent_from_target_sold(self, tmp_db):  # AC2
        row = _row(_trades(_cur([("L", 37)]), _tgt([])), "L")
        assert (row["action"], row["delta_shares"]) == ("SELL", -37)


# ---------------------------------------------------------------------------
# AC5: turnover treats the cover as a closure
# ---------------------------------------------------------------------------


def _rotation_book():
    """Long book of 10 rotating $500k in place: well over a 20% budget."""
    cur = [(f"L{i}", 1000) for i in range(10)]
    tgt = [
        {"ticker": f"L{i}", "shares": 1500 if i % 2 == 0 else 500, "combined_score": 50.0 + i}
        for i in range(10)
    ]
    return cur, tgt


class TestTurnover:
    def test_cover_to_zero_never_trimmed(self, tmp_db):  # AC5
        cur, tgt = _rotation_book()
        trades = _trades(_cur([*cur, ("S", -3000)]), _tgt(tgt))
        assert (trades["action"] == "HOLD").sum() > 0  # the budget binds on the rotation
        row = _row(trades, "S")
        assert (row["action"], row["delta_shares"], row["priority"]) == ("COVER", 3000, 1)

    def test_cover_to_zero_without_price_not_trimmed(self, tmp_db):
        # Adversarial: name left the scored universe, so Layer 4 has no prices for it.
        cur, tgt = _rotation_book()
        prices = {t: _px() for t, _ in cur}
        trades = _trades(_cur([*cur, ("GONE", -50)]), _tgt(tgt), prices=prices)
        row = _row(trades, "GONE")
        assert (row["action"], row["delta_shares"], row["price"]) == ("COVER", 50, 0.0)


# ---------------------------------------------------------------------------
# AC4-AC5: pre-trade and executor, end to end from generate_trades(commit=True)
# ---------------------------------------------------------------------------


def _pipeline(conn, tmp_path, current, halted=False, close=40.0):
    if close is not None:
        conn.execute(daily_prices.insert().values(ticker="S", date=_DATE, close=close))
        conn.commit()
    _trades(current, _tgt([]), conn=conn, commit=True)
    if halted:
        set_halt(tmp_path)
    pre = run_pre_trade(conn, _DATE, _CONFIG, tmp_path)
    client = MagicMock()
    client.get_all_positions.return_value = [MagicMock(symbol="S", qty=str(current.shares[0]))]
    client.get_orders.return_value = []
    client.submit_order.return_value = MagicMock(id="o-1", status="accepted", filled_qty="0")
    results = execute_approvals(conn, client, _DATE, _CONFIG, tmp_path, dry_run=False)
    return pre, client, results


class TestDownstream:
    def test_pre_trade_approves_as_closing_under_halt(self, tmp_db, tmp_path):  # AC5
        pre, _, _ = _pipeline(tmp_db, tmp_path, _cur([("S", -12)]), halted=True)
        assert pre.iloc[0][["action", "result"]].tolist() == ["COVER", "APPROVED"]
        assert pre.iloc[0]["reason"].startswith("CLOSING")

    def test_executor_submits_buy_to_close(self, tmp_db, tmp_path):  # AC4
        from alpaca.trading.enums import OrderSide, PositionIntent

        _, client, results = _pipeline(tmp_db, tmp_path, _cur([("S", -12)]))
        assert results[0]["status"] == "SUBMITTED"
        req = client.submit_order.call_args.args[0]
        assert (req.side, req.position_intent, req.qty) == (
            OrderSide.BUY,
            PositionIntent.BUY_TO_CLOSE,
            12,
        )
        assert float(req.limit_price) == pytest.approx(40.60)  # close 40 + 1.5% (packet 60)

    def test_no_close_skipped_not_dropped(self, tmp_db, tmp_path):
        # Adversarial: no stored close for the score date. The approval exists (APPROVED)
        # but the executor refuses to price it, loudly, rather than guess a limit.
        _, client, results = _pipeline(tmp_db, tmp_path, _cur([("S", -12)]), close=None)
        status = tmp_db.execute(sa.select(position_approvals.c.status)).scalar()
        assert status == "APPROVED"
        assert results[0]["reason"] == "no_close"
        client.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Adversarial cases
# ---------------------------------------------------------------------------


class TestAdversarial:
    def test_fractional_short_covered_whole_shares(self, tmp_db, tmp_path):
        # Book shows -12.4 shares: the cover is the full 12.4, ordered as 12 whole shares.
        row = _row(_trades(_cur([("S", -12.4)]), _tgt([])), "S")
        assert (row["action"], row["delta_shares"]) == ("COVER", pytest.approx(12.4))

    def test_sub_share_target_rounds_to_full_cover(self, tmp_db):
        target = _tgt([{"ticker": "S", "shares": -0.4, "direction": "SHORT"}])
        row = _row(_trades(_cur([("S", -12)]), target), "S")
        assert (row["action"], row["target_shares"], row["delta_shares"]) == ("COVER", 0, 12)

    def test_short_to_long_one_row_not_hold(self, tmp_db):
        target = _tgt([{"ticker": "X", "shares": 5}])
        trades = _trades(_cur([("X", -10)]), target)
        assert len(trades) == 1
        row = trades.iloc[0]
        assert (row["action"], row["delta_shares"], row["direction"]) == ("BUY", 15, "LONG")

    def test_duplicate_current_ticker_raises(self, tmp_db):
        # Last-wins would cover only one lot: refuse instead.
        with pytest.raises(ValueError, match="duplicate"):
            _trades(_cur([("S", -10), ("S", -5)]), _tgt([]))

    def test_duplicate_target_ticker_raises(self, tmp_db):
        target = _tgt([{"ticker": "S", "shares": -5}, {"ticker": "S", "shares": 0}])
        with pytest.raises(ValueError):
            _trades(_cur([("S", -10)]), target)
