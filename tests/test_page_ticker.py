"""Tests for dashboard/page_ticker.py — AC1, AC2, AC3, AC5, AC9, AC10."""

from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy as sa

from dashboard.page_ticker import _score_colour
from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL
from data.db import (
    daily_prices,
    earnings_calendar,
    insider_transactions,
    sp500_universe,
)

# ---------------------------------------------------------------------------
# AC5 — _score_colour helper
# ---------------------------------------------------------------------------


def test_score_colour_green():
    assert _score_colour(75) == LONG_COL


def test_score_colour_green_boundary():
    assert _score_colour(60) == LONG_COL


def test_score_colour_red():
    assert _score_colour(25) == SHORT_COL


def test_score_colour_red_boundary():
    assert _score_colour(40) == SHORT_COL


def test_score_colour_neutral():
    assert _score_colour(50) == NEUTRAL


def test_score_colour_none():
    assert _score_colour(None) == NEUTRAL


# ---------------------------------------------------------------------------
# AC1 — ticker list query
# ---------------------------------------------------------------------------


def test_ticker_list_query(tmp_engine):
    with tmp_engine.connect() as conn:
        conn.execute(
            sp500_universe.insert(),
            [
                {
                    "ticker": "MSFT",
                    "company_name": "Microsoft Corp",
                    "gics_sector": "IT",
                    "gics_sub_industry": "Software",
                    "updated_at": "2026-01-01",
                },
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc",
                    "gics_sector": "IT",
                    "gics_sub_industry": "Hardware",
                    "updated_at": "2026-01-01",
                },
            ],
        )
        conn.commit()

        rows = conn.execute(
            sa.select(
                sp500_universe.c.ticker,
                sp500_universe.c.company_name,
            ).order_by(sp500_universe.c.ticker)
        ).fetchall()

    tickers = [r[0] for r in rows]
    assert tickers == ["AAPL", "MSFT"]
    assert rows[0][1] == "Apple Inc"


# ---------------------------------------------------------------------------
# AC2 — price query returns empty DataFrame without raising
# ---------------------------------------------------------------------------


def test_price_chart_no_data_does_not_raise(tmp_engine):
    with tmp_engine.connect() as conn:
        rows = conn.execute(
            sa.select(daily_prices)
            .where(daily_prices.c.ticker == "UNKNOWN")
            .order_by(daily_prices.c.date)
        ).fetchall()

    df = pd.DataFrame(rows, columns=daily_prices.columns.keys())
    assert df.empty


# ---------------------------------------------------------------------------
# AC3 — price KPI calculations
# ---------------------------------------------------------------------------


def _make_price_df(n_rows: int = 260, base_price: float = 100.0) -> pd.DataFrame:
    """Build a synthetic price DataFrame with incrementing prices."""

    dates = pd.date_range(end="2026-06-19", periods=n_rows, freq="B")
    prices = [base_price + i * 0.1 for i in range(n_rows)]
    return pd.DataFrame(
        {
            "date": dates,
            "adj_close": prices,
            "volume": [1_000_000] * n_rows,
        }
    )


def test_price_kpi_52w():
    df = _make_price_df(260)
    tail_252 = df["adj_close"].tail(252)
    assert float(tail_252.max()) == pytest.approx(df["adj_close"].iloc[-1], rel=1e-6)
    assert float(tail_252.min()) < float(tail_252.max())


def test_ytd_return_positive():

    df = _make_price_df(260)
    df["date"] = pd.to_datetime(df["date"])
    current_year = int(df["date"].dt.year.max())
    ytd_df = df[df["date"].dt.year == current_year]
    latest_price = float(df["adj_close"].iloc[-1])
    ret = latest_price / float(ytd_df["adj_close"].iloc[0]) - 1
    assert ret > 0


def test_ytd_return_negative():

    # Flip prices so the latest is the lowest of the year
    n = 260
    dates = pd.date_range(end="2026-06-19", periods=n, freq="B")
    prices = [200.0 - i * 0.1 for i in range(n)]
    df = pd.DataFrame({"date": dates, "adj_close": prices, "volume": [1_000_000] * n})
    df["date"] = pd.to_datetime(df["date"])
    current_year = int(df["date"].dt.year.max())
    ytd_df = df[df["date"].dt.year == current_year]
    latest_price = float(df["adj_close"].iloc[-1])
    ret = latest_price / float(ytd_df["adj_close"].iloc[0]) - 1
    assert ret < 0


def test_ytd_return_single_row():
    dates = pd.to_datetime(["2026-06-19"])
    df = pd.DataFrame({"date": dates, "adj_close": [150.0], "volume": [1_000_000]})
    current_year = int(df["date"].dt.year.max())
    ytd_df = df[df["date"].dt.year == current_year]
    ret = (
        df["adj_close"].iloc[-1] / float(ytd_df["adj_close"].iloc[0]) - 1
        if len(ytd_df) > 1
        else 0.0
    )
    assert ret == 0.0


def test_avg_volume_30d():
    df = _make_price_df(260)
    vol_series = df["volume"].dropna()
    avg = int(vol_series.tail(30).mean())
    assert avg == 1_000_000


# ---------------------------------------------------------------------------
# AC9 — insider query limit
# ---------------------------------------------------------------------------


def test_insider_query_limit(tmp_engine):
    rows = [
        {
            "ticker": "AAPL",
            "insider_name": f"Person {i}",
            "insider_title": "CFO",
            "transaction_type": "Buy",
            "transaction_code": "P",
            "shares": 100.0,
            "price": 150.0,
            "date": f"2026-01-{i:02d}",
            "ownership_type": "D",
            "is_open_market": 1,
            "is_ceo_cfo": 0,
            "accession_no": f"ACC{i:04d}",
            "fetched_at": "2026-01-01",
        }
        for i in range(1, 16)  # 15 rows
    ]
    with tmp_engine.connect() as conn:
        conn.execute(insider_transactions.insert(), rows)
        conn.commit()

        result = conn.execute(
            sa.select(insider_transactions)
            .where(insider_transactions.c.ticker == "AAPL")
            .order_by(insider_transactions.c.date.desc())
            .limit(12)
        ).fetchall()

    assert len(result) == 12
    # Most recent date should appear first
    dates = [r[insider_transactions.columns.keys().index("date")] for r in result]
    assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# AC10 — empty universe guard
# ---------------------------------------------------------------------------


def test_empty_universe_returns_early(tmp_engine):
    with tmp_engine.connect() as conn:
        rows = conn.execute(sa.select(sp500_universe).order_by(sp500_universe.c.ticker)).fetchall()

    assert rows == []


def test_earnings_caption_query(tmp_engine):
    with tmp_engine.connect() as conn:
        conn.execute(
            earnings_calendar.insert().values(
                ticker="AAPL",
                earnings_date="2026-07-30",
                eps_estimate=1.45,
                fetched_at="2026-06-01",
            )
        )
        conn.commit()

        row = conn.execute(
            sa.select(earnings_calendar)
            .where(earnings_calendar.c.ticker == "AAPL")
            .order_by(earnings_calendar.c.earnings_date.desc())
            .limit(1)
        ).fetchone()

    assert row is not None
    er = dict(zip(earnings_calendar.columns.keys(), row, strict=False))
    assert er["earnings_date"] == "2026-07-30"
    assert er["eps_estimate"] == pytest.approx(1.45)


# ---------------------------------------------------------------------------
# Packet 18 AC10 — whole-page render smoke tests
# ---------------------------------------------------------------------------


def _ticker_app(db_url: str) -> None:
    from dashboard.page_ticker import render
    from data.db import get_engine

    cfg = {"institutional": {"tracked_funds": [{"name": "Citadel"}, {"name": "Point72"}]}}
    render(get_engine(db_url), cfg)


def _seed_universe(conn) -> None:
    conn.execute(
        sp500_universe.insert().values(
            ticker="AAPL",
            company_name="Apple Inc",
            gics_sector="IT",
            gics_sub_industry="Hardware",
            updated_at="2026-01-01",
        )
    )


def _seed_all(conn) -> None:
    from analysis.db import ai_scores, analysis_results, combined_scores
    from data.db import (
        analyst_estimates,
        earnings_transcripts,
        insider_cluster_flags,
        institutional_holdings,
        institutional_summary,
        sec_filings,
        short_interest,
    )
    from factors.db import factor_scores

    conn.execute(
        daily_prices.insert(),
        [
            {"ticker": "AAPL", "date": f"2026-06-{d:02d}", "adj_close": 280.0 + d, "volume": 1}
            for d in range(1, 20)
        ],
    )
    conn.execute(
        factor_scores.insert().values(
            ticker="AAPL",
            score_date="2026-10-13",
            regime="NORMAL",
            mom_12_1=70.0,
            institutional_score=93.0,
            composite_score=15.0,
            direction="SHORT",
        )
    )
    conn.execute(
        ai_scores.insert().values(ticker="AAPL", score_date="2026-10-13", ai_composite=5.0)
    )
    conn.execute(
        combined_scores.insert().values(
            ticker="AAPL",
            score_date="2026-10-13",
            quant_composite=14.7,
            ai_composite=float("nan"),
            combined_score=18.7,
            direction="SHORT",
        )
    )
    conn.execute(
        analysis_results.insert(),
        [
            {
                "analyzer": "insider",
                "ticker": "AAPL",
                "artifact_id": "AAPL_2026-10-13",
                "model": "gpt-4o-mini",
                "result_json": '{"signal_strength": "STRONG_SELL", "one_line_summary": "$5 sold"}',
                "created_at": "2026-10-13T22:09:10+00:00",
            },
            {
                "analyzer": "risk",
                "ticker": "AAPL",
                "artifact_id": "AAPL_x",
                "model": "gpt-4o",
                "result_json": "not json",
                "created_at": "2026-10-10T19:13:39+00:00",
            },
        ],
    )
    conn.execute(
        sec_filings.insert(),
        [
            {
                "ticker": "AAPL",
                "form_type": form,
                "filed_date": "2026-10-01",
                "accession_no": f"ACC-{form}",
                "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/x/",
            }
            for form in ("10-K", "8-K", "4")
        ],
    )
    conn.execute(
        earnings_transcripts.insert().values(
            ticker="AAPL", earnings_date="2026-07-30", quarter="Q2", year=2026, content="Rev $1B"
        )
    )
    conn.execute(
        analyst_estimates.insert(),
        [
            {"ticker": "AAPL", "date": d, "price_target": p, "eps_estimate_fwd": 7.0}
            for d, p in (("2026-09-01", 300.0), ("2026-10-01", 310.0))
        ],
    )
    conn.execute(
        short_interest.insert(),
        [
            {"ticker": "AAPL", "date": d, "short_pct_float": 0.01, "short_ratio": 1.5}
            for d in ("2026-09-15", "2026-09-30")
        ],
    )
    conn.execute(
        insider_cluster_flags.insert().values(
            ticker="AAPL",
            window_start="2026-09-30",
            window_end="2026-10-30",
            insider_count=3,
            total_shares=604.0,
        )
    )
    conn.execute(
        institutional_holdings.insert(),
        [
            {
                "fund_name": f,
                "ticker": "AAPL",
                "shares_held": s,
                "market_value": s * 289.36,
                "report_date": p,
                "filed_date": "2026-08-14",
            }
            for f, s, p in (
                ("Citadel", 7_000_000, "2026-03-31"),
                ("Citadel", 7_296_076, "2026-06-30"),
                ("Point72", 100, "2026-06-30"),
            )
        ],
    )
    conn.execute(
        institutional_summary.insert().values(
            ticker="AAPL",
            report_date="2026-06-30",
            funds_holding=2,
            net_share_change=296_076.0,
            new_positions=0,
        )
    )


class TestRenderSmoke:
    def _run(self, tmp_path, seed):
        from streamlit.testing.v1 import AppTest

        from data.db import get_engine, initialise_schema

        url = f"sqlite:///{tmp_path / 'app.db'}"
        engine = get_engine(url)
        initialise_schema(engine)
        with engine.connect() as conn:
            seed(conn)
            conn.commit()
        engine.dispose()
        at = AppTest.from_function(_ticker_app, args=(url,), default_timeout=30)
        return at.run()

    def _text(self, at) -> str:
        return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)

    def test_renders_with_only_universe(self, tmp_path):
        at = self._run(tmp_path, _seed_universe)
        assert not at.exception
        assert "No institutional holdings among tracked funds." in self._text(at)

    def test_renders_with_all_sections(self, tmp_path):
        def seed(conn):
            _seed_universe(conn)
            _seed_all(conn)

        at = self._run(tmp_path, seed)
        assert not at.exception
        text = self._text(at)
        for header in ("SIGNAL", "INSTITUTIONAL HOLDINGS", "SEC FILINGS"):
            assert header in text
        labels = [e.label for e in at.expander]
        assert "Sub-factor scores" in labels
        assert "Earnings release - Q2 2026 (2026-07-30)" in labels
        assert "Insider analysis — \\$5 sold" in labels
        assert "Risk analysis" in labels
        assert "Stored analysis could not be read." in text

        cards = " ".join(m.value for m in at.markdown if "metric-card" in m.value)
        assert ">2 / 2<" in cards  # AC1 funds holding
        assert ">+296,076<" not in cards and ">+0.30M<" in cards  # AC1 net change
        assert f'color:{SHORT_COL}">SHORT<' in cards  # AC3 direction
        assert '<div class="label">AI</div><div class="value" style="color:#' in cards
        assert ">—<" in cards  # AC3 NaN AI composite

        frames = [df.value for df in at.dataframe]
        filings = next(f for f in frames if "Form" in f.columns)
        assert sorted(filings["Form"]) == ["10-K", "8-K"]  # AC6: Form 4 excluded
        funds = next(f for f in frames if "Fund" in f.columns)
        assert list(funds["Fund"]) == ["Citadel", "Point72"]  # AC2

        assert len(at.get("plotly_chart")) == 4  # price, factor bars, two AC8 trends
        assert "Insider cluster: 2026-09-30 to 2026-10-30, 3 insiders, 604 shares" in text
