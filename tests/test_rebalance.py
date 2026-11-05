"""Tests for portfolio/rebalance.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pandas as pd

import portfolio.db  # noqa: F401
from portfolio.rebalance import generate_trades

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {
    "portfolio": {
        "nav_usd": 1_000_000,
        "turnover_budget_pct": 0.20,
        "transaction_costs": {
            "spread_hl_fraction": 0.05,
            "market_impact_coef": 0.10,
        },
        "adv_lookback_days": 5,
    }
}


def _make_price_df(n=10, close=100.0, high=101.0, low=99.0, volume=10_000):
    return pd.DataFrame(
        {
            "close": [float(close)] * n,
            "high": [float(high)] * n,
            "low": [float(low)] * n,
            "volume": [float(volume)] * n,
        }
    )


def _prices_for(*tickers):
    return {t: _make_price_df() for t in tickers}


def _empty_current():
    return pd.DataFrame(columns=["ticker", "shares"])


def _current_df(rows):
    """rows: list of (ticker, shares)"""
    return pd.DataFrame(rows, columns=["ticker", "shares"])


def _target_df(rows):
    """rows: list of dicts with keys matching target columns."""
    defaults = {
        "direction": "LONG",
        "combined_score": 75.0,
        "current_price": 100.0,
        "sector": "Technology",
        "beta": 1.0,
        "weight": 0.05,
    }
    records = []
    for row in rows:
        r = dict(defaults)
        r.update(row)
        records.append(r)
    return pd.DataFrame(records)


def _run(current, target, prices=None, config=None, conn=None, score_date="2026-03-01"):
    if prices is None:
        tickers = set()
        if not current.empty and "ticker" in current.columns:
            tickers |= set(current["ticker"])
        if not target.empty and "ticker" in target.columns:
            tickers |= set(target["ticker"])
        prices = _prices_for(*tickers)
    if config is None:
        config = _CONFIG
    return generate_trades(
        current,
        target,
        prices,
        config,
        conn=conn,
        score_date=score_date,
        commit=False,
    )


# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------


class TestActionClassification:
    def test_new_position_is_buy(self, tmp_db):
        current = _empty_current()
        target = _target_df([{"ticker": "AAPL", "shares": 100}])
        trades = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "AAPL"].iloc[0]
        assert row["action"] == "BUY"

    def test_closed_long_is_sell(self, tmp_db):
        current = _current_df([("AAPL", 100)])
        target = _empty_current()
        trades = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "AAPL"].iloc[0]
        assert row["action"] == "SELL"

    def test_new_short_is_short(self, tmp_db):
        current = _empty_current()
        target = _target_df([{"ticker": "MSFT", "shares": -50, "direction": "SHORT"}])
        trades = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "MSFT"].iloc[0]
        assert row["action"] == "SHORT"

    def test_covered_short_is_cover(self, tmp_db):
        """Reducing a short (current=-50, target=-10) → delta=+40 → COVER."""
        current = _current_df([("MSFT", -50)])
        target = _target_df([{"ticker": "MSFT", "shares": -10, "direction": "SHORT"}])
        trades = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "MSFT"].iloc[0]
        assert row["action"] == "COVER"

    def test_hold_when_delta_below_threshold(self, tmp_db):
        """Delta of 0.5 shares is below 1-share threshold → HOLD."""
        current = _current_df([("AAPL", 100.0)])
        target = _target_df([{"ticker": "AAPL", "shares": 100.5}])
        trades = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "AAPL"].iloc[0]
        assert row["action"] == "HOLD"


# ---------------------------------------------------------------------------
# Turnover budget
# ---------------------------------------------------------------------------


def _rotation(n=10, base=1000, step=500, direction="LONG"):
    """A full book rotating in place: gross unchanged, half the names up, half down."""
    sign = 1 if direction == "LONG" else -1
    prefix = "L" if direction == "LONG" else "S"
    tickers = [f"{prefix}{i}" for i in range(n)]
    current = [(t, sign * base) for t in tickers]
    target = [
        {
            "ticker": t,
            "shares": sign * (base + step if i % 2 == 0 else base - step),
            "direction": direction,
            "combined_score": float(50 + i * 3) if sign > 0 else float(50 - i * 3),
        }
        for i, t in enumerate(tickers)
    ]
    return current, target


def _value(trades, mask=None):
    rows = trades if mask is None else trades[mask]
    return (rows["delta_shares"].abs() * 100.0).sum()


class TestTurnoverBudget:
    def test_turnover_budget_trims_smallest_score_changes(self, tmp_db):
        """Full long book rotating 50% of NAV in place (gross unchanged), budget 20%:
        the rotation is capped at $200k and the rest is trimmed (packet 57: a build-up
        from cash is no longer trimmed, so the rotation case is the one that binds)."""
        current, target = _rotation()
        trades = _run(_current_df(current), _target_df(target), conn=tmp_db)

        held = trades[trades["action"] == "HOLD"]
        assert len(held) > 0
        assert _value(trades) <= 200_000 * 1.05

    def test_closures_are_never_trimmed(self, tmp_db):
        """Full closures (target_shares=0) must not be downgraded to HOLD."""
        # Start with 3 positions, close all
        current = _current_df([("A", 5000), ("B", 5000), ("C", 5000)])
        target = _empty_current()
        prices = _prices_for("A", "B", "C")
        trades = _run(current, target, prices=prices, conn=tmp_db)
        actions = set(trades[trades["ticker"].isin(["A", "B", "C"])]["action"])
        assert "HOLD" not in actions

    def test_budget_split_preserves_both_books(self, tmp_db):
        """A long-book rotation must not starve a short-book rotation.

        Each book rotates $300k in place (gross unchanged); budget 20% = $200k is split
        between the books by trade value, so both keep some trades.
        """
        cur_l, tgt_l = _rotation(n=6)
        cur_s, tgt_s = _rotation(n=6, direction="SHORT")
        trades = _run(_current_df(cur_l + cur_s), _target_df(tgt_l + tgt_s), conn=tmp_db)

        active_longs = trades[(trades["action"] != "HOLD") & (trades["direction"] == "LONG")]
        active_shorts = trades[(trades["action"] != "HOLD") & (trades["direction"] == "SHORT")]
        assert len(active_shorts) > 0, "SHORTs were starved by LONG book"
        assert len(active_longs) > 0, "LONGs should also appear"


def _cfg(nav=1_000_000, budget=0.20):
    return {"portfolio": {**_CONFIG["portfolio"], "nav_usd": nav, "turnover_budget_pct": budget}}


def _book_from_cash(nav=100_000.0):
    """2026-11-04 shape: 20 long / 20 short at 3.75% of NAV each, 150% gross."""
    rows = []
    for i in range(40):
        long = i < 20
        rows.append(
            {
                "ticker": f"T{i:02d}",
                "shares": (1 if long else -1) * 0.0375 * nav / 100.0,
                "direction": "LONG" if long else "SHORT",
                "combined_score": 95.0 - i * 0.1 if long else 5.0 + i * 0.1,
            }
        )
    return _target_df(rows)


class TestBuildUpAllowance:
    """Packet 57: the build-up to target gross does not spend the rotation budget."""

    def test_build_from_cash_not_trimmed(self, tmp_db):
        # Investigation repro (test_build_from_cash_not_capped_by_turnover_budget): 6 trades.
        trades = _run(_empty_current(), _book_from_cash(), config=_cfg(100_000, 0.30))
        assert (trades["action"] != "HOLD").sum() == 40

    def test_full_book_rotation_still_capped(self, tmp_db):
        current, target = _rotation()
        # X closes and Y opens at the same size: gross unchanged, so the closure is rotation
        # (kept, and spends the budget first) rather than de-risking.
        current.append(("X", 300))
        target.append({"ticker": "Y", "shares": 300, "combined_score": 99.0})
        trades = _run(_current_df(current), _target_df(target), conn=tmp_db)
        assert trades.loc[trades["ticker"] == "X", "action"].item() == "SELL"
        assert _value(trades) <= 200_000 + 1e-6
        assert (trades["action"] == "HOLD").sum() > 0

    def test_partial_book_gap_per_side(self, tmp_db):
        current = [(f"L{i}", 100) for i in range(4)] + [(f"S{i}", -100) for i in range(2)]
        target = [{"ticker": f"L{i}", "shares": 500} for i in range(10)] + [
            {"ticker": f"S{i}", "shares": -400, "direction": "SHORT", "combined_score": 20.0}
            for i in range(10)
        ]
        trades = _run(_current_df(current), _target_df(target), conn=tmp_db)
        assert (trades["action"] != "HOLD").sum() == 20

    def test_long_gap_not_spent_by_short_book(self, tmp_db):
        cur_l, tgt_l = _rotation()  # long book full, rotating $500k
        tgt_s = [
            {"ticker": f"S{i}", "shares": -500, "direction": "SHORT", "combined_score": 10.0}
            for i in range(4)
        ]  # short book empty -> $200k build-up
        trades = _run(_current_df(cur_l), _target_df(tgt_l + tgt_s), conn=tmp_db)
        shorts = trades["direction"] == "SHORT"
        assert (trades.loc[shorts, "action"] == "SHORT").all()
        assert _value(trades, ~shorts) <= 200_000 + 1e-6
        assert (trades.loc[~shorts, "action"] == "HOLD").sum() > 0

    def test_de_risking_reductions_not_trimmed(self, tmp_db):
        current = [(f"L{i}", 1500) for i in range(10)]
        target = [{"ticker": f"L{i}", "shares": 1200} for i in range(10)]
        trades = _run(_current_df(current), _target_df(target), conn=tmp_db)
        assert (trades["action"] == "SELL").sum() == 10

    def test_all_closures_pass(self, tmp_db):
        current = _current_df([(f"L{i}", 1000) for i in range(5)])
        trades = _run(current, _empty_current(), conn=tmp_db)
        assert (trades["action"] == "SELL").all()

    def test_short_dropped_from_target_is_covered(self, tmp_db):
        # Packet 61: was a strict xfail (map_action returned HOLD for cur < 0, tgt == 0).
        trades = _run(_current_df([("S0", -2000)]), _empty_current(), conn=tmp_db)
        assert trades["action"].tolist() == ["COVER"]

    def test_missing_price_no_allowance(self, tmp_db):
        current, target = _rotation()
        target.append({"ticker": "NOPX", "shares": 5000})
        prices = _prices_for(*[t for t, _ in current])
        prices["NOPX"] = pd.DataFrame()
        trades = _run(_current_df(current), _target_df(target), prices=prices, conn=tmp_db)
        assert _value(trades, trades["ticker"] != "NOPX") <= 200_000 + 1e-6

    def test_exact_fit_kept(self, tmp_db):
        target = _target_df([{"ticker": "A", "shares": 2000}])
        trades = _run(_empty_current(), target, config=_cfg(budget=0.0), conn=tmp_db)
        assert trades["action"].tolist() == ["BUY"]

    def test_small_gap_cannot_unlock_rotation(self, tmp_db):
        current, target = _rotation()
        for i, row in enumerate(target):
            if i % 2:
                row["shares"] += 20  # +100 shares in total = $10k = 1% of NAV
        trades = _run(_current_df(current), _target_df(target), conn=tmp_db)
        assert _value(trades) <= 210_000 + 1e-6

    def test_offsetting_side_gaps_counted_per_side(self, tmp_db):
        current = [(f"L{i}", 500) for i in range(4)] + [(f"S{i}", -1000) for i in range(4)]
        target = [{"ticker": f"L{i}", "shares": 1000} for i in range(4)] + [
            {"ticker": f"S{i}", "shares": -500, "direction": "SHORT", "combined_score": 10.0}
            for i in range(4)
        ]
        trades = _run(
            _current_df(current), _target_df(target), config=_cfg(budget=0.0), conn=tmp_db
        )
        assert sorted(trades["action"]) == ["BUY"] * 4 + ["COVER"] * 4

    def test_short_gap_uses_absolute_value(self, tmp_db):
        current = _current_df([("S0", -300)])
        target = _target_df(
            [
                {"ticker": "S0", "shares": -300, "direction": "SHORT"},
                {"ticker": "S1", "shares": -400, "direction": "SHORT"},
            ]
        )
        trades = _run(current, target, config=_cfg(budget=0.0), conn=tmp_db)
        assert trades.set_index("ticker").loc["S1", "action"] == "SHORT"

    def test_zero_nav_no_trim(self, tmp_db):
        current, target = _rotation()
        trades = _run(_current_df(current), _target_df(target), config=_cfg(nav=0), conn=tmp_db)
        assert (trades["action"] != "HOLD").all()


class TestTrimmedRows:
    def _commit(self, conn, score_date="2026-03-01"):
        current, target = _rotation()
        return generate_trades(
            _current_df(current),
            _target_df(target),
            _prices_for(*[t for t, _ in current]),
            _CONFIG,
            conn=conn,
            score_date=score_date,
            commit=True,
        )

    def _rows(self, conn, status):
        import sqlalchemy as sa

        from portfolio.db import position_approvals

        return conn.execute(
            sa.select(position_approvals).where(position_approvals.c.status == status)
        ).fetchall()

    def test_trimmed_rows_written(self, tmp_db):
        trades = self._commit(tmp_db)
        trimmed = self._rows(tmp_db, "TRIMMED")
        assert len(trimmed) == (trades["action"] == "HOLD").sum() > 0
        for row in trimmed:
            assert row.action in ("BUY", "SELL")
            assert row.delta_shares == row.target_shares - row.current_shares != 0
        assert len(self._rows(tmp_db, "PENDING")) == (trades["action"] != "HOLD").sum()

    def test_whatif_writes_nothing(self, tmp_db):
        import sqlalchemy as sa

        from portfolio.db import position_approvals

        current, target = _rotation()
        _run(_current_df(current), _target_df(target), conn=tmp_db)
        count = sa.select(sa.func.count()).select_from(position_approvals)
        assert tmp_db.execute(count).scalar() == 0

    def test_rerun_appends(self, tmp_db):
        # Pins today's append-only behaviour: a re-run for the same date duplicates rows.
        self._commit(tmp_db)
        first = len(self._rows(tmp_db, "TRIMMED"))
        self._commit(tmp_db)
        assert len(self._rows(tmp_db, "TRIMMED")) == 2 * first

    def test_summary_attached(self, tmp_db):
        current, target = _rotation()
        trades = _run(_current_df(current), _target_df(target), conn=tmp_db)
        summary = trades.attrs["turnover"]
        assert summary["needed_usd"] == 500_000
        assert summary["allowance_usd"] == 0
        assert summary["kept_usd"] + summary["trimmed_usd"] == 500_000
        assert summary["trimmed_count"] == (trades["action"] == "HOLD").sum()


# ---------------------------------------------------------------------------
# Priority column
# ---------------------------------------------------------------------------


class TestPriorityColumn:
    def test_priority_column_is_sequential(self, tmp_db):
        current = _current_df([("AAPL", 100), ("MSFT", -50)])
        target = _target_df(
            [
                {"ticker": "AAPL", "shares": 150},
                {"ticker": "MSFT", "shares": -80, "direction": "SHORT"},
                {"ticker": "GOOG", "shares": 200},
            ]
        )
        prices = _prices_for("AAPL", "MSFT", "GOOG")
        trades = _run(current, target, prices=prices, conn=tmp_db)
        prio = sorted(trades["priority"].tolist())
        assert prio == list(range(1, len(trades) + 1))


# ---------------------------------------------------------------------------
# Commit (packet 44): approvals only
# ---------------------------------------------------------------------------


class TestCommit:
    def test_commit_writes_approvals_only(self, tmp_db):
        import sqlalchemy as sa

        from portfolio.db import portfolio_history, portfolio_positions, position_approvals

        target = _target_df([{"ticker": "AAPL", "shares": 100.0}])
        generate_trades(
            _empty_current(),
            target,
            _prices_for("AAPL"),
            _CONFIG,
            conn=tmp_db,
            score_date="2026-03-01",
            commit=True,
        )
        assert tmp_db.execute(sa.select(sa.func.count()).select_from(position_approvals)).scalar()
        for table in (portfolio_positions, portfolio_history):
            count = tmp_db.execute(sa.select(sa.func.count()).select_from(table)).scalar()
            assert count == 0, table.name


# ---------------------------------------------------------------------------
# Whole-share targets (packet 56)
# ---------------------------------------------------------------------------


class TestWholeShareTargets:
    def test_new_position_target_rounded(self, tmp_db):
        target = _target_df([{"ticker": "AAPL", "shares": 19.6}])
        row = _run(_empty_current(), target, conn=tmp_db).iloc[0]
        assert row["target_shares"] == 20
        assert row["delta_shares"] == 20

    def test_cover_target_rounded(self, tmp_db):
        # 2026-10-29 CLX: book -43, optimiser target -19.95.
        current = _current_df([("CLX", -43)])
        target = _target_df([{"ticker": "CLX", "shares": -19.95, "direction": "SHORT"}])
        row = _run(current, target, conn=tmp_db).iloc[0]
        assert row["target_shares"] == -20
        assert row["delta_shares"] == 23
        assert row["action"] == "COVER"

    def test_sub_share_target_is_full_close(self, tmp_db):
        # Adversarial: a 0.4-share target becomes a clean full close, not a 0.4 residue.
        current = _current_df([("AAPL", 10)])
        target = _target_df([{"ticker": "AAPL", "shares": 0.4}])
        row = _run(current, target, conn=tmp_db).iloc[0]
        assert (row["action"], row["target_shares"], row["delta_shares"]) == ("SELL", 0, -10)
