"""ROE and debt/equity at ingestion and the --recompute-ratios backfill (packet 42 AC8, AC9)."""

import pytest
import sqlalchemy as sa

import run_data
from data.db import fundamentals
from data.fundamentals import _compute_ratios, recompute_ratios
from tests.test_run_data_args import stubbed_run_data  # noqa: F401 — pytest fixture


def _period(**overrides):
    return {
        "revenue": 100.0,
        "gross_profit": 40.0,
        "operating_income": 20.0,
        "net_income": 15.0,
        "total_equity": 60.0,
        "total_assets": 200.0,
        "total_debt": 30.0,
        "current_assets": 60.0,
        "current_liabilities": 30.0,
        "accounts_receivable": 10.0,
        "cfo": 20.0,
        **overrides,
    }


class TestEquityRatios:
    def test_roe_is_net_income_over_equity(self):
        # AAPL 2026-06-30: stored -0.72 before packet 42
        r = _compute_ratios(_period(net_income=29_789e6, total_equity=107_520e6), None, None)
        assert r["roe"] == pytest.approx(0.2771, abs=1e-4)

    @pytest.mark.parametrize("equity", [-5.0, 0.0, None])
    def test_no_positive_equity_gives_none(self, equity):
        r = _compute_ratios(_period(total_equity=equity), None, None)
        assert r["roe"] is None and r["debt_to_equity"] is None

    def test_zero_debt_is_zero(self):
        assert _compute_ratios(_period(total_debt=0.0), None, None)["debt_to_equity"] == 0.0

    def test_negative_income_kept(self):
        assert _compute_ratios(_period(net_income=-6.0), None, None)["roe"] == pytest.approx(-0.1)


def _insert(conn, end, **values):
    row = {
        "ticker": "AAPL",
        "period_type": "quarterly",
        "period_end": end,
        "net_income": 29_789e6,
        "total_equity": 107_520e6,
        "total_debt": 84_344e6,
        "roe": -0.7229,
        "debt_to_equity": 0.7845,
        "revenue": 109_417e6,
        **values,
    }
    conn.execute(fundamentals.insert().values(**row))
    conn.commit()


def _stored(conn):
    rows = conn.execute(
        sa.select(
            fundamentals.c.period_end,
            fundamentals.c.roe,
            fundamentals.c.debt_to_equity,
            fundamentals.c.revenue,
        ).order_by(fundamentals.c.period_end)
    ).fetchall()
    return [tuple(r) for r in rows]


class TestRecomputeRatios:
    def test_rewrites_roe_and_debt_to_equity(self, tmp_db):
        _insert(tmp_db, "2026-06-30")
        _insert(tmp_db, "2026-03-31", total_equity=-2e9, roe=-15.9, debt_to_equity=-42.0)
        assert recompute_ratios(tmp_db) == 2
        (neg, pos) = _stored(tmp_db)
        assert neg[1:3] == (None, None)
        assert pos[1] == pytest.approx(29_789 / 107_520)
        assert pos[2] == pytest.approx(84_344 / 107_520)

    def test_other_columns_unchanged(self, tmp_db):
        _insert(tmp_db, "2026-06-30")
        recompute_ratios(tmp_db)
        assert _stored(tmp_db)[0][3] == pytest.approx(109_417e6)

    def test_idempotent(self, tmp_db):
        _insert(tmp_db, "2026-06-30")
        recompute_ratios(tmp_db)
        assert recompute_ratios(tmp_db) == 0

    # Adversarial: a row already correct is not rewritten, and missing figures clear the ratio
    def test_correct_row_untouched_and_missing_figures_cleared(self, tmp_db):
        _insert(tmp_db, "2026-06-30", roe=29_789 / 107_520, debt_to_equity=84_344 / 107_520)
        _insert(tmp_db, "2026-03-31", net_income=None, total_debt=None)
        assert recompute_ratios(tmp_db) == 1
        assert _stored(tmp_db)[0][1:3] == (None, None)


class TestCli:
    def test_flag_runs_only_the_recompute(self, stubbed_run_data, monkeypatch):  # noqa: F811
        calls = []
        monkeypatch.setattr(run_data, "recompute_ratios", lambda conn: calls.append(1) or 0)
        monkeypatch.setattr("sys.argv", ["run_data.py", "--recompute-ratios"])
        run_data.main()
        assert calls == [1]
        assert stubbed_run_data == []  # no insider rebuild either

    def test_rejects_tickers(self, stubbed_run_data, monkeypatch):  # noqa: F811
        monkeypatch.setattr("sys.argv", ["run_data.py", "--recompute-ratios", "--tickers", "AAPL"])
        with pytest.raises(SystemExit):
            run_data.main()

    def test_rejects_rebuild_insider(self, stubbed_run_data, monkeypatch):  # noqa: F811
        monkeypatch.setattr("sys.argv", ["run_data.py", "--recompute-ratios", "--rebuild-insider"])
        with pytest.raises(SystemExit):
            run_data.main()
        assert stubbed_run_data == []

    def test_off_by_default(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["run_data.py"])
        assert run_data._parse_args().recompute_ratios is False
