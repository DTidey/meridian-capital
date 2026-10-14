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
            ticker="AAPL",
            earnings_date="2026-07-30",
            quarter="Q2",
            year=2026,
            content="Rev $1B",
            source_accession_no="0000320193-26-000018",
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


# ---------------------------------------------------------------------------
# Packet 20 — filing viewer (EDGAR fetch and LLM faked)
# ---------------------------------------------------------------------------


def _seed_viewer(conn) -> None:
    _seed_universe(conn)
    _seed_all(conn)


class TestDocumentViewer:
    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        import streamlit as st
        from streamlit.testing.v1 import AppTest

        import dashboard.ticker_documents as td
        from analysis import doc_summary
        from data import edgar_docs
        from data.db import get_engine, initialise_schema

        st.cache_data.clear()
        self.fetched = []

        def fake_fetch(url, accession, form_type):
            self.fetched.append((url, accession, form_type))
            return "" if accession == "FAIL" else "Revenue \\$1B\n\n| a | b |\n|---|---|\n| 1 | 2 |"

        self.stream_error = None

        def fake_stream(cfg, form_type, text, on_usage):
            self.prompt = (form_type, text)
            if self.stream_error:
                yield "partial "
                raise self.stream_error
            on_usage(type("U", (), {"prompt_tokens": 100, "completion_tokens": 10})())
            yield "## Results\n"
            yield "Revenue $1B and 999 units"

        monkeypatch.setattr(edgar_docs, "fetch_filing_markdown", fake_fetch)
        monkeypatch.setattr(doc_summary, "stream_summary", fake_stream)
        monkeypatch.setattr(td, "get_api_key", lambda cfg: "key")

        url = f"sqlite:///{tmp_path / 'app.db'}"
        engine = get_engine(url)
        initialise_schema(engine)
        with engine.connect() as conn:
            _seed_viewer(conn)
            conn.commit()
        self.engine = engine
        at = AppTest.from_function(_ticker_app, args=(url,), default_timeout=30)
        return at.run()

    def _viewer(self, at):
        return next(s for s in at.selectbox if s.label == "Read a filing")

    def test_options_release_first_then_filings_no_form_4(self, app):
        viewer = self._viewer(app)
        assert viewer.options == [
            "Earnings release - Q2 2026 (2026-07-30)",
            "2026-10-01 10-K",
            "2026-10-01 8-K",
        ]
        assert viewer.value is None
        assert self.fetched == []  # nothing fetched until a filing is chosen

    def test_release_uses_8k_folder_from_cik(self, app):
        self._viewer(app).select_index(0).run()
        assert not app.exception
        assert self.fetched[0] == (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/",
            "0000320193-26-000018",
            "8-K",
        )
        assert [t.label for t in app.tabs] == ["AI summary", "Document"]
        assert any("Revenue" in m.value for m in app.markdown)

    def test_generate_summary_saves_and_flags_figures(self, app):
        self._viewer(app).select_index(1).run()
        app.button(key="summarise_ACC-10-K").click().run()
        assert not app.exception
        assert self.prompt[0] == "10-K"
        assert any("Revenue \\$1B" in m.value for m in app.markdown)
        captions = " ".join(c.value for c in app.caption)
        assert "999" in captions  # not in the source, so flagged

        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT analyzer, artifact_id, result_json, cost_usd FROM analysis_results "
                    "WHERE analyzer='doc_summary'"
                )
            ).fetchone()
        assert row[1] == "ACC-10-K"
        assert '"unverified": ["999"]' in row[2]
        assert row[3] > 0

    def test_saved_summary_shown_without_button(self, app):
        self._viewer(app).select_index(1).run()
        app.button(key="summarise_ACC-10-K").click().run()
        app.run()
        assert not [b for b in app.button if b.key == "summarise_ACC-10-K"]
        assert any("Results" in m.value for m in app.markdown)

    def test_fetch_failure_falls_back_to_stored_text(self, app):
        with self.engine.connect() as conn:
            conn.execute(
                sa.text(
                    "UPDATE sec_filings SET accession_no='FAIL', content_text='stored words' "
                    "WHERE form_type='8-K'"
                )
            )
            conn.commit()
        app.run()
        self._viewer(app).select_index(2).run()
        assert not app.exception
        assert any(t.value == "stored words" for t in app.text_area)
        assert "Couldn't fetch the original" in " ".join(c.value for c in app.caption)

    def test_button_disabled_without_key(self, app, monkeypatch):
        import dashboard.ticker_documents as td

        monkeypatch.setattr(td, "get_api_key", lambda cfg: "")
        self._viewer(app).select_index(1).run()
        assert app.button(key="summarise_ACC-10-K").disabled

    def test_llm_error_shows_caption_and_saves_nothing(self, app):
        self.stream_error = RuntimeError("provider down")
        self._viewer(app).select_index(1).run()
        app.button(key="summarise_ACC-10-K").click().run()
        assert not app.exception
        assert "Summary failed: provider down" in " ".join(c.value for c in app.caption)
        assert not any("partial" in m.value for m in app.markdown)
        with self.engine.connect() as conn:
            n = conn.execute(
                sa.text("SELECT count(*) FROM analysis_results WHERE analyzer='doc_summary'")
            ).scalar()
        assert n == 0

    def test_truncated_source_caption(self, app, monkeypatch):
        from analysis import doc_summary

        monkeypatch.setattr(doc_summary, "MAX_SOURCE_TOKENS", 2)
        self._viewer(app).select_index(1).run()
        app.button(key="summarise_ACC-10-K").click().run()
        assert "covers its first part only" in " ".join(c.value for c in app.caption)

    def test_saved_summary_provenance_caption(self, app):
        self._viewer(app).select_index(1).run()
        app.button(key="summarise_ACC-10-K").click().run()
        app.run()
        captions = " ".join(c.value for c in app.caption)
        assert "gpt-4o-mini" in captions
        assert "\\$0." in captions or "$0." in captions

    def test_failed_fetch_not_cached(self, app):
        with self.engine.connect() as conn:
            conn.execute(
                sa.text("UPDATE sec_filings SET accession_no='FAIL' WHERE form_type='8-K'")
            )
            conn.commit()
        app.run()
        self._viewer(app).select_index(2).run()
        app.run()
        assert [f for f in self.fetched if f[1] == "FAIL"].__len__() >= 2  # retried, not cached

    def test_same_label_filings_both_selectable(self, app, monkeypatch):
        # AppTest maps selections through labels, so drive the viewer directly
        import dashboard.ticker_documents as td

        with self.engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO sec_filings (ticker, form_type, filed_date, accession_no, "
                    "filing_url) VALUES ('AAPL', '8-K', '2026-10-01', 'ACC-8-K-2', "
                    "'https://www.sec.gov/Archives/edgar/data/320193/y/')"
                )
            )
            conn.commit()
        options = td.viewer_options(self.engine, "AAPL")
        dupes = [i for i, o in enumerate(options) if o["label"] == "2026-10-01 8-K"]
        assert len(dupes) == 2

        opened = []
        monkeypatch.setattr(td, "_render_document", lambda engine, option: opened.append(option))
        monkeypatch.setattr(td, "_render_summary", lambda *a: None)
        monkeypatch.setattr(td.st, "tabs", lambda labels: [_NullCtx(), _NullCtx()])
        for i in dupes:
            monkeypatch.setattr(td.st, "selectbox", lambda *a, _i=i, **k: list(a[1])[_i])
            td.render_document_viewer(self.engine, "AAPL", {})
        assert {o["accession"] for o in opened} == {"ACC-8-K", "ACC-8-K-2"}


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
