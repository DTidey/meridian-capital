"""Tests for analysis/combined_score.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
from analysis.combined_score import compute_ai_composite, compute_combined_scores
from analysis.db import ai_scores as ai_scores_table, combined_scores as combined_scores_table
from factors.db import factor_scores as factor_scores_table
from data.db import get_engine, initialise_schema, sp500_universe


@pytest.fixture
def tmp_engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tmp_db(tmp_engine):
    conn = tmp_engine.connect()
    yield conn
    conn.close()


def _insert_quant(conn, ticker, score, sector="Technology"):
    conn.execute(factor_scores_table.insert().values(
        ticker=ticker,
        score_date="2024-06-30",
        composite_score=score,
        direction="NEUTRAL",
        sector=sector,
    ))
    conn.commit()


def _insert_universe(conn, ticker, sector="Technology"):
    conn.execute(sp500_universe.insert().values(
        ticker=ticker,
        company_name=f"{ticker} Inc",
        gics_sector=sector,
        gics_sub_industry=sector,
        updated_at="2024-01-01",
    ))
    conn.commit()


class TestComputeAiComposite:
    def test_all_analyzers_averages_scores(self, tmp_db):
        earnings = {"management_confidence": {"score": 8}, "revenue_guidance": {"score": 8},
                    "margin_trajectory": {"score": 8}, "competitive_position": {"score": 8},
                    "risk_factors": {"score": 8}, "capital_allocation": {"score": 8}}
        filing  = {"earnings_quality_score": 8.0, "balance_sheet_score": 8.0}
        risk    = {"risk_severity": "LOW"}    # maps to 10
        insider = {"signal_strength": "STRONG_BUY"}  # maps to 10

        result = compute_ai_composite(tmp_db, "AAPL", "2024-06-30",
                                      earnings, filing, risk, insider)
        assert result["analyzers_used"] == 4
        assert result["ai_composite"] == pytest.approx((8.0 + 8.0 + 10.0 + 10.0) / 4)

    def test_none_analyzers_excluded(self, tmp_db):
        result = compute_ai_composite(tmp_db, "MSFT", "2024-06-30",
                                      None, None, None, None)
        assert result["analyzers_used"] == 0
        assert result["ai_composite"] is None

    def test_partial_analyzers(self, tmp_db):
        risk = {"risk_severity": "MEDIUM"}  # maps to 8
        result = compute_ai_composite(tmp_db, "GOOG", "2024-06-30",
                                      None, None, risk, None)
        assert result["analyzers_used"] == 1
        assert result["ai_composite"] == pytest.approx(8.0)

    def test_persisted_to_db(self, tmp_db):
        import sqlalchemy as sa
        compute_ai_composite(tmp_db, "NVDA", "2024-06-30", None, None, None, None)
        row = tmp_db.execute(
            sa.select(ai_scores_table).where(ai_scores_table.c.ticker == "NVDA")
        ).fetchone()
        assert row is not None


class TestComputeCombinedScores:
    def test_empty_quant_returns_empty(self, tmp_db):
        df = compute_combined_scores(tmp_db, "2024-06-30", {})
        assert df.empty

    def test_pure_quant_when_no_ai(self, tmp_db):
        for t, s in [("A", 80.0), ("B", 50.0), ("C", 20.0),
                     ("D", 70.0), ("E", 30.0), ("F", 60.0)]:
            _insert_quant(tmp_db, t, s)

        config = {"scoring": {"long_quintile_threshold": 80, "short_quintile_threshold": 20,
                               "min_sector_size": 1},
                  "analysis": {"combined_score": {"quant_weight": 0.60, "ai_weight": 0.40}}}
        df = compute_combined_scores(tmp_db, "2024-06-30", config)
        assert len(df) == 6
        # tickers with no AI should still have combined_score
        assert df["combined_score"].notna().all()

    def test_ai_weight_blended(self, tmp_db):
        _insert_quant(tmp_db, "X", 60.0, sector="Financials")
        _insert_quant(tmp_db, "Y", 40.0, sector="Financials")

        import sqlalchemy as sa
        now = "2024-06-30T00:00:00+00:00"
        tmp_db.execute(ai_scores_table.insert().values(
            ticker="X", score_date="2024-06-30",
            ai_composite=9.0, analyzers_used=2, computed_at=now,
        ))
        tmp_db.execute(ai_scores_table.insert().values(
            ticker="Y", score_date="2024-06-30",
            ai_composite=3.0, analyzers_used=2, computed_at=now,
        ))
        tmp_db.commit()

        config = {"scoring": {"long_quintile_threshold": 80, "short_quintile_threshold": 20,
                               "min_sector_size": 1},
                  "analysis": {"combined_score": {"quant_weight": 0.60, "ai_weight": 0.40}}}
        df = compute_combined_scores(tmp_db, "2024-06-30", config).set_index("ticker")
        # X has better AI, so combined_raw should be higher for X
        # quant: X=60, Y=40; ai_norm: X=(9-1)/9*100≈88.9, Y=(3-1)/9*100≈22.2
        # combined_raw: X=0.6*60+0.4*88.9=71.6, Y=0.6*40+0.4*22.2=32.9
        assert df.loc["X", "combined_score"] > df.loc["Y", "combined_score"]

    def test_directions_assigned(self, tmp_db):
        # 10 tickers: top 2 (80th+ pct) → LONG, bottom 2 (≤20th pct) → SHORT
        scores = [("L1", 95), ("L2", 85), ("N1", 70), ("N2", 60),
                  ("N3", 50), ("N4", 40), ("N5", 30), ("N6", 25), ("S1", 15), ("S2", 5)]
        for t, s in scores:
            _insert_quant(tmp_db, t, float(s))

        config = {"scoring": {"long_quintile_threshold": 80, "short_quintile_threshold": 20,
                               "min_sector_size": 1},
                  "analysis": {"combined_score": {"quant_weight": 1.0, "ai_weight": 0.0}}}
        df = compute_combined_scores(tmp_db, "2024-06-30", config).set_index("ticker")
        assert df.loc["L1", "direction"] == "LONG"
        assert df.loc["S2", "direction"] == "SHORT"
        assert df.loc["N3", "direction"] == "NEUTRAL"

    def test_persisted_to_combined_scores_table(self, tmp_db):
        import sqlalchemy as sa
        _insert_quant(tmp_db, "P", 70.0)
        config = {"scoring": {"long_quintile_threshold": 80, "short_quintile_threshold": 20,
                               "min_sector_size": 1},
                  "analysis": {"combined_score": {"quant_weight": 1.0, "ai_weight": 0.0}}}
        compute_combined_scores(tmp_db, "2024-06-30", config)
        row = tmp_db.execute(
            sa.select(combined_scores_table).where(combined_scores_table.c.ticker == "P")
        ).fetchone()
        assert row is not None
