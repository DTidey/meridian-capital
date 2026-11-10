"""Tests for the read-only churn replay (packet 64, AC9)."""

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import sqlalchemy as sa
import yaml

import analysis.db  # noqa: F401
from analysis.db import ai_scores, combined_scores
from data.db import daily_prices, get_engine, initialise_schema
from factors.db import factor_scores
from portfolio import churn_replay, churn_sim

_CFG = {
    "portfolio": {
        "num_longs": 2,
        "num_shorts": 2,
        "target_long_gross": 0.8,
        "target_short_gross": 0.7,
    }
}


def _night(longs: list[str], shorts=("S1", "S2", "S3")) -> pd.DataFrame:
    """Candidates in rank order (first = strongest)."""
    rows = [
        {"ticker": t, "direction": "LONG", "combined_score": 99.0 - i} for i, t in enumerate(longs)
    ]
    rows += [
        {"ticker": t, "direction": "SHORT", "combined_score": 1.0 + i} for i, t in enumerate(shorts)
    ]
    return pd.DataFrame(rows)


# Night 1: B falls to rank 4 (rank exit after 1 night). Night 3: C leaves the band (held 2).
_HISTORY = [
    _night(["A", "B", "C", "D"]),
    _night(["A", "C", "D", "B"]),
    _night(["A", "B", "C", "D"]),
    _night(["A", "B", "D"]),
]


def test_metrics_on_fixture_history():
    got = churn_sim.simulate(_HISTORY, _CFG, exit_rank=3)
    assert got["nights"] == 3
    assert got["exits"] == 2
    assert (got["rank_exits"], got["band_exits"], got["flip_exits"]) == (1, 1, 0)
    assert (got["round_trips_1"], got["round_trips_2"]) == (1, 2)
    assert got["turnover_per_night"] == pytest.approx((0.4 + 0.0 + 0.4) / 3)
    # Night 2 holds A (1) and C (3): mean side rank over 4 nights x 2 sides
    assert got["mean_side_rank"] == pytest.approx((1.5 * 4 + 2.0 + 1.5 * 3) / 8)


def test_baseline_matches_top_n():
    """Exit rank = num_* rebuilds the strict top N every night."""
    got = churn_sim.simulate(_HISTORY, _CFG, exit_rank=2)
    # Night 1: B out (C in); night 2: C out (B in); night 3: nothing (A, B top 2)
    assert got["exits"] == 2
    assert got["round_trips_1"] == 2
    assert got["mean_side_rank"] == pytest.approx(1.5)


def test_flip_counted_separately():
    history = [_night(["A", "B", "C"]), _night(["A", "C"], shorts=("B", "S1", "S2"))]
    got = churn_sim.simulate(history, _CFG, exit_rank=3)
    assert got["flip_exits"] == 1 and got["exits"] == 1


def test_spearman_pairs():
    a = pd.Series({"X": 1.0, "Y": 2.0, "Z": 3.0})
    b = pd.Series({"X": 3.0, "Y": 2.0, "Z": 1.0, "W": 9.0})
    assert churn_sim.spearman_pairs([a, a, b]) == pytest.approx([1.0, -1.0])


# ---------------------------------------------------------------------------
# Read-only DB replay
# ---------------------------------------------------------------------------

_DATES = ["2026-11-05", "2026-11-06", "2026-11-09"]
_TABLES = (factor_scores, ai_scores, combined_scores, daily_prices)


@pytest.fixture
def history_engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'replay.db'}")
    initialise_schema(engine)
    with engine.begin() as conn:
        for n, day in enumerate(_DATES):
            _seed_day(conn, day, n)
        for day, close in zip(_DATES, (28.18, 25.64, 19.92), strict=True):
            conn.execute(sa.insert(daily_prices).values(ticker="^VIX", date=day, close=close))
    yield engine
    engine.dispose()


def _seed_day(conn, day: str, shift: int) -> None:
    tickers = [f"T{i:02d}" for i in range(20)]
    for i, t in enumerate(tickers):
        score = float((i * 5 + shift * 7) % 100)
        row = dict.fromkeys(churn_sim.SCORE_COLS, score)
        conn.execute(
            sa.insert(factor_scores).values(
                ticker=t,
                score_date=day,
                sector="Tech",
                regime="NORMAL",
                composite_score=score,
                **row,
            )
        )
        conn.execute(
            sa.insert(ai_scores).values(
                ticker=t, score_date=day, ai_composite=50.0 + i, analyzers_used=2
            )
        )
        direction = "LONG" if score >= 80 else "SHORT" if score <= 20 else "NEUTRAL"
        conn.execute(
            sa.insert(combined_scores).values(
                ticker=t, score_date=day, combined_score=score, direction=direction
            )
        )


def _counts(engine) -> dict:
    with engine.connect() as conn:
        return {
            t.name: conn.execute(sa.select(sa.func.count()).select_from(t)).scalar()
            for t in _TABLES
        }


def _config() -> dict:
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    cfg["portfolio"].update(num_longs=2, num_shorts=2)
    return cfg


def test_read_only_transaction(history_engine):
    """The replay's connection cannot write, and a full run changes no row counts."""
    before = _counts(history_engine)
    args = churn_replay.parse_args(
        [
            "--since",
            _DATES[0],
            "--until",
            _DATES[-1],
            "--exit-ranks",
            "2,3",
            "--smoothing-days",
            "1,5",
            "--low-bands",
            "12-18",
        ]
    )
    with history_engine.connect() as conn:
        score_rows, churn_rows = churn_replay.run_replay(conn, _config(), args)
    assert _counts(history_engine) == before
    assert [r["scoring"] for r in score_rows] == [
        "stored",
        "switch (recomputed)",
        "blend 1d low 12-18",
        "blend 5d low 12-18",
    ]
    assert len(churn_rows) == 8 and all(r["nights"] == 2 for r in churn_rows)

    with history_engine.connect() as conn:
        churn_replay.set_read_only(conn)
        with pytest.raises(sa.exc.OperationalError):
            conn.execute(sa.delete(combined_scores))


def test_postgres_read_only_statement_first():
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    churn_replay.set_read_only(conn)
    stmt = conn.execute.call_args_list[0].args[0]
    assert str(stmt) == "SET TRANSACTION READ ONLY"


def test_unknown_dialect_refused():
    conn = MagicMock()
    conn.dialect.name = "mysql"
    with pytest.raises(RuntimeError, match="read-only"):
        churn_replay.set_read_only(conn)


def test_switch_rescore_matches_stored_regime_weights(history_engine):
    """The recomputed hard switch scores each night with the stored regime's weights."""
    with history_engine.connect() as conn:
        churn_replay.set_read_only(conn)
        history = churn_replay.load_history(conn, _DATES[0], _DATES[-1])
    assert history["dates"] == _DATES
    scoring = churn_replay.scorings(_config(), [5], [(12.0, 18.0)])[1]
    scored = churn_replay.score_nights(history, scoring, _config())
    assert len(scored["nights"]) == 3
    for night in scored["nights"]:
        assert set(night["direction"]) <= {"LONG", "SHORT"}
        assert night.loc[night["direction"] == "LONG", "combined_score"].between(80, 100).all()
