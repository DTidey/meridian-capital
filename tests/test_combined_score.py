"""Tests for analysis/combined_score.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
from analysis.combined_score import compute_ai_composite, compute_combined_scores
from analysis.db import ai_scores as ai_scores_table
from analysis.db import combined_scores as combined_scores_table
from data.db import get_engine, initialise_schema, sp500_universe
from factors.db import factor_scores as factor_scores_table


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


_DATE = "2024-06-30"
_CONFIG = {
    "scoring": {"long_quintile_threshold": 80, "short_quintile_threshold": 20},
    "analysis": {"combined_score": {"quant_weight": 0.60, "ai_weight": 0.40}},
}


def _direction(score):
    return "LONG" if score >= 80 else "SHORT" if score <= 20 else "NEUTRAL"


def _insert_quant(conn, ticker, score, sector="Technology", direction=None):
    conn.execute(
        factor_scores_table.insert().values(
            ticker=ticker,
            score_date=_DATE,
            composite_score=score,
            direction=direction or _direction(score),
            sector=sector,
        )
    )
    conn.commit()


def _insert_ai(conn, ticker, ai_composite, analyzers_used=2):
    conn.execute(
        ai_scores_table.insert().values(
            ticker=ticker,
            score_date=_DATE,
            ai_composite=ai_composite,
            analyzers_used=analyzers_used,
            computed_at="2024-06-30T00:00:00+00:00",
        )
    )
    conn.commit()


def _config(quant_weight=0.60, ai_weight=0.40):
    return {
        "scoring": _CONFIG["scoring"],
        "analysis": {"combined_score": {"quant_weight": quant_weight, "ai_weight": ai_weight}},
    }


def _combined(conn, config=None):
    return compute_combined_scores(conn, _DATE, config or _CONFIG).set_index("ticker")


def _insert_universe(conn, ticker, sector="Technology"):
    conn.execute(
        sp500_universe.insert().values(
            ticker=ticker,
            company_name=f"{ticker} Inc",
            gics_sector=sector,
            gics_sub_industry=sector,
            updated_at="2024-01-01",
        )
    )
    conn.commit()


class TestComputeAiComposite:
    def test_all_analyzers_averages_scores(self, tmp_db):
        earnings = {
            "management_confidence": {"score": 8},
            "revenue_guidance": {"score": 8},
            "margin_trajectory": {"score": 8},
            "competitive_position": {"score": 8},
            "risk_factors": {"score": 8},
            "capital_allocation": {"score": 8},
        }
        filing = {"earnings_quality_score": 8.0, "balance_sheet_score": 8.0}
        risk = {"material_risks": [{"risk": "a", "severity": "LOW"}]}  # maps to 10
        insider = {"signal_strength": "STRONG_BUY"}  # maps to 10

        result = compute_ai_composite(tmp_db, "AAPL", "2024-06-30", earnings, filing, risk, insider)
        assert result["analyzers_used"] == 4
        assert result["ai_composite"] == pytest.approx((8.0 + 8.0 + 10.0 + 10.0) / 4)

    def test_none_analyzers_excluded(self, tmp_db):
        result = compute_ai_composite(tmp_db, "MSFT", "2024-06-30", None, None, None, None)
        assert result["analyzers_used"] == 0
        assert result["ai_composite"] is None

    def test_partial_analyzers(self, tmp_db):
        risk = {"material_risks": [{"risk": "a", "severity": "MEDIUM"}]}  # maps to 8
        result = compute_ai_composite(tmp_db, "GOOG", "2024-06-30", None, None, risk, None)
        assert result["analyzers_used"] == 1
        assert result["ai_composite"] == pytest.approx(8.0)

    def test_persisted_to_db(self, tmp_db):
        import sqlalchemy as sa

        compute_ai_composite(tmp_db, "NVDA", "2024-06-30", None, None, None, None)
        row = tmp_db.execute(
            sa.select(ai_scores_table).where(ai_scores_table.c.ticker == "NVDA")
        ).fetchone()
        assert row is not None


class TestDirection:
    def test_direction_follows_quant(self, tmp_db):
        for t, s in [("L1", 95.0), ("L2", 81.0), ("N1", 50.0), ("S1", 19.0), ("S2", 3.0)]:
            _insert_quant(tmp_db, t, s)
        df = _combined(tmp_db)
        assert df["direction"].to_dict() == {
            "L1": "LONG",
            "L2": "LONG",
            "N1": "NEUTRAL",
            "S1": "SHORT",
            "S2": "SHORT",
        }

    def test_neutral_name_never_promoted_by_bad_ai_on_candidates(self, tmp_db):
        # C9: candidates with poor AI used to be overtaken by unreviewed names near 80/20.
        _insert_quant(tmp_db, "L", 82.0)
        _insert_quant(tmp_db, "S", 18.0)
        _insert_quant(tmp_db, "NHI", 79.9)
        _insert_quant(tmp_db, "NLO", 20.1)
        _insert_ai(tmp_db, "L", 1.0)
        _insert_ai(tmp_db, "S", 10.0)
        df = _combined(tmp_db)
        assert df.loc["NHI", "direction"] == "NEUTRAL"
        assert df.loc["NLO", "direction"] == "NEUTRAL"

    def test_candidate_never_demoted_by_bad_ai(self, tmp_db):
        _insert_quant(tmp_db, "L", 80.0)
        _insert_quant(tmp_db, "S", 20.0)
        _insert_ai(tmp_db, "L", 1.0)
        _insert_ai(tmp_db, "S", 10.0)
        df = _combined(tmp_db)
        assert df.loc["L", "direction"] == "LONG"
        assert df.loc["S", "direction"] == "SHORT"

    def test_sector_does_not_affect_direction(self, tmp_db):
        # Adversarial: the old sector re-rank made the best of five NEUTRAL names LONG.
        for i, s in enumerate([40.0, 45.0, 50.0, 55.0, 60.0]):
            _insert_quant(tmp_db, f"U{i}", s, sector="Utilities")
        df = _combined(tmp_db)
        assert (df["direction"] == "NEUTRAL").all()
        assert df.loc["U4", "combined_score"] == pytest.approx(60.0)


class TestBands:
    def test_scores_within_side_bands(self, tmp_db):
        for i, s in enumerate([99.0, 95.0, 90.0, 85.0, 80.0]):
            _insert_quant(tmp_db, f"L{i}", s)
            _insert_ai(tmp_db, f"L{i}", 1.0 + 2 * i)
        for i, s in enumerate([20.0, 15.0, 10.0, 5.0, 1.0]):
            _insert_quant(tmp_db, f"S{i}", s)
            _insert_ai(tmp_db, f"S{i}", 1.0 + 2 * i)
        df = _combined(tmp_db)
        longs = df[df["direction"] == "LONG"]["combined_score"]
        shorts = df[df["direction"] == "SHORT"]["combined_score"]
        assert longs.between(80, 100).all()
        assert shorts.between(0, 20).all()

    def test_neutral_keeps_quant_score(self, tmp_db):
        _insert_quant(tmp_db, "N", 63.2)
        _insert_quant(tmp_db, "L", 90.0)
        _insert_ai(tmp_db, "L", 9.0)
        df = _combined(tmp_db)
        assert df.loc["N", "combined_score"] == pytest.approx(63.2)

    def test_unnormalised_weights_stay_in_band(self, tmp_db):
        _insert_quant(tmp_db, "L1", 99.0)
        _insert_quant(tmp_db, "L2", 81.0)
        _insert_ai(tmp_db, "L1", 10.0)
        _insert_ai(tmp_db, "L2", 1.0)
        df = _combined(tmp_db, _config(0.6, 0.6))
        assert df["combined_score"].between(80, 100).all()


class TestOrdering:
    def test_better_ai_ranks_long_higher(self, tmp_db):
        _insert_quant(tmp_db, "A", 90.0)
        _insert_quant(tmp_db, "B", 90.0)
        _insert_ai(tmp_db, "A", 8.0)
        _insert_ai(tmp_db, "B", 4.0)
        df = _combined(tmp_db)
        assert df.loc["A", "combined_score"] > df.loc["B", "combined_score"]

    def test_better_ai_ranks_short_lower(self, tmp_db):
        # A lower AI composite is a stronger short, so it scores further below 20.
        _insert_quant(tmp_db, "A", 10.0)
        _insert_quant(tmp_db, "B", 10.0)
        _insert_ai(tmp_db, "A", 3.0)
        _insert_ai(tmp_db, "B", 7.0)
        df = _combined(tmp_db)
        assert df.loc["A", "combined_score"] < df.loc["B", "combined_score"]

    def test_zero_ai_weight_is_quant_order(self, tmp_db):
        for t, q, ai in [("A", 99.0, 1.0), ("B", 90.0, 5.0), ("C", 81.0, 10.0)]:
            _insert_quant(tmp_db, t, q)
            _insert_ai(tmp_db, t, ai)
        df = _combined(tmp_db, _config(1.0, 0.0))
        assert df.loc[["A", "B", "C"], "combined_score"].is_monotonic_decreasing

    def test_zero_ai_weight_is_quant_order_for_shorts(self, tmp_db):
        # The lowest quant composite is the strongest short, so it scores lowest.
        for t, q, ai in [("A", 2.0, 10.0), ("B", 10.0, 5.0), ("C", 18.0, 1.0)]:
            _insert_quant(tmp_db, t, q)
            _insert_ai(tmp_db, t, ai)
        df = _combined(tmp_db, _config(1.0, 0.0))
        assert df.loc[["A", "B", "C"], "combined_score"].is_monotonic_increasing

    def test_zero_quant_weight_is_ai_order(self, tmp_db):
        for t, q, ai in [("A", 99.0, 1.0), ("B", 90.0, 5.0), ("C", 81.0, 10.0)]:
            _insert_quant(tmp_db, t, q)
            _insert_ai(tmp_db, t, ai)
        df = _combined(tmp_db, _config(0.0, 1.0))
        assert df.loc[["A", "B", "C"], "combined_score"].is_monotonic_increasing

    def test_blend_matches_formula(self, tmp_db):
        # Two longs: quant ranks A=75, B=25; AI ranks A=25, B=75 (mid-rank (r - 0.5) / n).
        _insert_quant(tmp_db, "A", 95.0)
        _insert_quant(tmp_db, "B", 85.0)
        _insert_ai(tmp_db, "A", 4.0)
        _insert_ai(tmp_db, "B", 6.0)
        df = _combined(tmp_db)
        assert df.loc["A", "combined_score"] == pytest.approx(80 + 0.2 * (0.6 * 75 + 0.4 * 25))
        assert df.loc["B", "combined_score"] == pytest.approx(80 + 0.2 * (0.6 * 25 + 0.4 * 75))

    def test_blend_matches_formula_for_shorts(self, tmp_db):
        # Three shorts, strength = lower value: quant ranks A=83.3, B=50, C=16.7;
        # AI ranks A=83.3, B=16.7, C=50.
        for t, q, ai in [("A", 2.0, 2.0), ("B", 10.0, 8.0), ("C", 18.0, 5.0)]:
            _insert_quant(tmp_db, t, q)
            _insert_ai(tmp_db, t, ai)
        df = _combined(tmp_db)
        hi, mid, lo = 250 / 3, 50.0, 50 / 3
        assert df.loc["A", "combined_score"] == pytest.approx(20 - 0.2 * (0.6 * hi + 0.4 * hi))
        assert df.loc["B", "combined_score"] == pytest.approx(20 - 0.2 * (0.6 * mid + 0.4 * lo))
        assert df.loc["C", "combined_score"] == pytest.approx(20 - 0.2 * (0.6 * lo + 0.4 * mid))

    def test_raw_ai_scale_does_not_matter(self, tmp_db):
        # Adversarial: bunched AI (5.9 vs 6.1) moves a long as far as 1 vs 10; only ranks count.
        _insert_quant(tmp_db, "A", 90.0)
        _insert_quant(tmp_db, "B", 90.0)
        _insert_ai(tmp_db, "A", 6.1)
        _insert_ai(tmp_db, "B", 5.9)
        bunched = _combined(tmp_db)["combined_score"]
        assert bunched["A"] - bunched["B"] == pytest.approx(0.2 * 0.4 * 50)


class TestMissingAi:
    def test_candidate_without_ai_row_gets_neutral_rank(self, tmp_db):
        _insert_quant(tmp_db, "A", 90.0)
        _insert_quant(tmp_db, "B", 90.0)
        _insert_quant(tmp_db, "C", 90.0)
        _insert_ai(tmp_db, "A", 9.0)
        _insert_ai(tmp_db, "B", 2.0)
        df = _combined(tmp_db)
        assert df.loc["C", "direction"] == "LONG"
        # Equal quant ranks (50); AI ranks A=75, B=25, C=50.
        assert df.loc["C", "combined_score"] == pytest.approx(80 + 0.2 * 50)
        assert df.loc["B", "combined_score"] < df.loc["C", "combined_score"]
        assert df.loc["C", "combined_score"] < df.loc["A", "combined_score"]

    def test_zero_analyzers_treated_as_missing(self, tmp_db):
        # Adversarial: a stale ai_composite on a row with analyzers_used == 0 is ignored.
        _insert_quant(tmp_db, "A", 90.0)
        _insert_quant(tmp_db, "B", 90.0)
        _insert_ai(tmp_db, "A", 10.0, analyzers_used=0)
        _insert_ai(tmp_db, "B", 2.0)
        df = _combined(tmp_db)
        assert df.loc["A", "combined_score"] == pytest.approx(80 + 0.2 * 50)
        assert df.loc["B", "combined_score"] == pytest.approx(80 + 0.2 * 50)

    def test_single_candidate_side(self, tmp_db):
        _insert_quant(tmp_db, "L", 90.0)
        _insert_quant(tmp_db, "S", 10.0)
        _insert_ai(tmp_db, "L", 9.0)
        df = _combined(tmp_db)
        assert df.loc["L", "combined_score"] == pytest.approx(90.0)
        assert df.loc["S", "combined_score"] == pytest.approx(10.0)

    def test_no_ai_results_at_all(self, tmp_db):
        for t, q in [("A", 99.0), ("B", 90.0), ("C", 81.0), ("S", 5.0)]:
            _insert_quant(tmp_db, t, q)
        df = _combined(tmp_db)
        assert df.loc[["A", "B", "C"], "combined_score"].is_monotonic_decreasing
        assert df["combined_score"].notna().all()


class TestPersistence:
    def test_empty_quant_returns_empty(self, tmp_db):
        df = compute_combined_scores(tmp_db, _DATE, {})
        assert df.empty

    def test_rows_written(self, tmp_db):
        import sqlalchemy as sa

        _insert_quant(tmp_db, "P", 70.0)
        _insert_quant(tmp_db, "L", 90.0)
        _insert_ai(tmp_db, "L", 7.5)
        compute_combined_scores(tmp_db, _DATE, _CONFIG)
        rows = {r.ticker: r for r in tmp_db.execute(sa.select(combined_scores_table)).fetchall()}
        assert set(rows) == {"P", "L"}
        assert rows["L"].direction == "LONG"
        assert rows["L"].ai_composite == pytest.approx(7.5)
        assert rows["P"].combined_score == pytest.approx(70.0)
