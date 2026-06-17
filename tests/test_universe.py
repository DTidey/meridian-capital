"""Universe — cache freshness, Wikipedia parse, benchmark loading, deduplication."""

from datetime import datetime, timedelta

import responses as rsps
import sqlalchemy as sa

from data.db import benchmark_tickers, insert_or_replace, sp500_universe
from data.universe import _cache_is_fresh, fetch_sp500, get_all_tickers, load_benchmarks

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Minimal Wikipedia HTML that produces the expected columns
_WIKI_HTML = """\
<html><body>
<table id="constituents">
<thead>
<tr>
  <th>Symbol</th><th>Security</th>
  <th>GICS Sector</th><th>GICS Sub-Industry</th>
  <th>Other</th>
</tr>
</thead>
<tbody>
<tr>
  <td>AAPL</td><td>Apple Inc.</td>
  <td>Information Technology</td><td>Technology Hardware</td>
  <td>extra</td>
</tr>
<tr>
  <td>BRK.B</td><td>Berkshire Hathaway</td>
  <td>Financials</td><td>Diversified Financials</td>
  <td>extra</td>
</tr>
<tr>
  <td>MSFT</td><td>Microsoft</td>
  <td>Information Technology</td><td>Systems Software</td>
  <td>extra</td>
</tr>
</tbody>
</table>
</body></html>
"""


def _seed_universe(conn, tickers):
    """Insert universe rows with an updated_at timestamp."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        insert_or_replace(conn, sp500_universe),
        [
            {"ticker": t, "company_name": t, "gics_sector": "Technology", "updated_at": now}
            for t in tickers
        ],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _cache_is_fresh
# ---------------------------------------------------------------------------


class TestCacheIsFresh:
    def test_empty_table_is_not_fresh(self, tmp_db):
        assert _cache_is_fresh(tmp_db, 7) is False

    def test_just_inserted_is_fresh(self, tmp_db):
        _seed_universe(tmp_db, ["AAPL"])
        assert _cache_is_fresh(tmp_db, 7) is True

    def test_old_record_is_stale(self, tmp_db):
        stale = (datetime.utcnow() - timedelta(days=10)).isoformat(timespec="seconds")
        tmp_db.execute(
            sa.insert(sp500_universe).values(
                ticker="AAPL",
                company_name="Apple",
                updated_at=stale,
            )
        )
        tmp_db.commit()
        assert _cache_is_fresh(tmp_db, 7) is False

    def test_exactly_at_boundary_is_stale(self, tmp_db):
        boundary = (datetime.utcnow() - timedelta(days=7, seconds=1)).isoformat(timespec="seconds")
        tmp_db.execute(
            sa.insert(sp500_universe).values(
                ticker="AAPL",
                company_name="Apple",
                updated_at=boundary,
            )
        )
        tmp_db.commit()
        assert _cache_is_fresh(tmp_db, 7) is False


# ---------------------------------------------------------------------------
# fetch_sp500
# ---------------------------------------------------------------------------


class TestFetchSp500:
    @rsps.activate
    def test_fetches_and_stores_tickers(self, tmp_db, config):
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        tickers = fetch_sp500(tmp_db, config)
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    @rsps.activate
    def test_dot_normalized_to_hyphen(self, tmp_db, config):
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        tickers = fetch_sp500(tmp_db, config)
        assert "BRK-B" in tickers
        assert "BRK.B" not in tickers

    @rsps.activate
    def test_stores_sector_data(self, tmp_db, config):
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        fetch_sp500(tmp_db, config)
        row = tmp_db.execute(
            sa.select(sp500_universe.c.gics_sector, sp500_universe.c.gics_sub_industry).where(
                sp500_universe.c.ticker == "AAPL"
            )
        ).fetchone()
        assert row[0] == "Information Technology"
        assert row[1] == "Technology Hardware"

    @rsps.activate
    def test_fresh_cache_skips_network(self, tmp_db, config):
        _seed_universe(tmp_db, ["AAPL", "MSFT", "GOOG"])
        # No HTTP mock registered — would raise ConnectionError if called
        tickers = fetch_sp500(tmp_db, config)
        assert "AAPL" in tickers

    @rsps.activate
    def test_force_refreshes_stale_cache(self, tmp_db, config):
        _seed_universe(tmp_db, ["AAPL"])
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        tickers = fetch_sp500(tmp_db, config, force=True)
        assert "MSFT" in tickers  # Added by fresh fetch

    @rsps.activate
    def test_network_failure_falls_back_to_cache(self, tmp_db, config):
        _seed_universe(tmp_db, ["AAPL", "MSFT"])
        # Simulate HTTP error
        rsps.add(rsps.GET, _WIKI_URL, body=Exception("network down"))
        tickers = fetch_sp500(tmp_db, config, force=True)
        assert "AAPL" in tickers  # Stale cache returned

    @rsps.activate
    def test_removed_ticker_deleted_on_refresh(self, tmp_db, config):
        # CTRA was in the DB but is not in the fresh Wikipedia list
        _seed_universe(tmp_db, ["AAPL", "CTRA"])
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        tickers = fetch_sp500(tmp_db, config, force=True)
        assert "CTRA" not in tickers
        count = tmp_db.execute(
            sa.select(sa.func.count())
            .select_from(sp500_universe)
            .where(sp500_universe.c.ticker == "CTRA")
        ).scalar()
        assert count == 0

    @rsps.activate
    def test_removed_ticker_not_deleted_on_cache_hit(self, tmp_db, config):
        # Cache is fresh — no network call, so stale rows should not be pruned
        _seed_universe(tmp_db, ["AAPL", "CTRA"])
        tickers = fetch_sp500(tmp_db, config)  # no force, cache is fresh
        assert "CTRA" in tickers  # still present, no delete without a real refresh

    @rsps.activate
    def test_removed_ticker_not_deleted_on_network_failure(self, tmp_db, config):
        _seed_universe(tmp_db, ["AAPL", "CTRA"])
        rsps.add(rsps.GET, _WIKI_URL, body=Exception("network down"))
        tickers = fetch_sp500(tmp_db, config, force=True)
        assert "CTRA" in tickers  # fallback cache returned unchanged


# ---------------------------------------------------------------------------
# load_benchmarks
# ---------------------------------------------------------------------------


class TestLoadBenchmarks:
    def test_all_categories_stored(self, tmp_db, config):
        load_benchmarks(tmp_db, config)
        rows = tmp_db.execute(
            sa.select(benchmark_tickers.c.ticker, benchmark_tickers.c.category)
        ).fetchall()
        categories = {r[1] for r in rows}
        assert "broad_market" in categories
        assert "sector_etf" in categories
        assert "other" in categories

    def test_expected_broad_market_tickers(self, tmp_db, config):
        load_benchmarks(tmp_db, config)
        tickers = set(
            tmp_db.execute(
                sa.select(benchmark_tickers.c.ticker).where(
                    benchmark_tickers.c.category == "broad_market"
                )
            )
            .scalars()
            .all()
        )
        assert {"SPY", "QQQ", "IWM", "DIA"} <= tickers

    def test_idempotent(self, tmp_db, config):
        load_benchmarks(tmp_db, config)
        load_benchmarks(tmp_db, config)
        count = tmp_db.execute(sa.select(sa.func.count()).select_from(benchmark_tickers)).scalar()
        expected = (
            len(config["universe"]["benchmark_tickers"]["broad_market"])
            + len(config["universe"]["benchmark_tickers"]["sector_etfs"])
            + len(config["universe"]["benchmark_tickers"]["other"])
        )
        assert count == expected


# ---------------------------------------------------------------------------
# get_all_tickers
# ---------------------------------------------------------------------------


class TestGetAllTickers:
    @rsps.activate
    def test_combines_and_deduplicates(self, tmp_db, config):
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        tickers = get_all_tickers(tmp_db, config)
        # No duplicates
        assert len(tickers) == len(set(tickers))
        # Universe + benchmarks both present
        assert "AAPL" in tickers
        assert "SPY" in tickers

    @rsps.activate
    def test_benchmarks_added_to_universe(self, tmp_db, config):
        rsps.add(rsps.GET, _WIKI_URL, body=_WIKI_HTML, content_type="text/html")
        tickers = get_all_tickers(tmp_db, config)
        benchmark_tickers = (
            config["universe"]["benchmark_tickers"]["broad_market"]
            + config["universe"]["benchmark_tickers"]["sector_etfs"]
            + config["universe"]["benchmark_tickers"]["other"]
        )
        for t in benchmark_tickers:
            assert t in tickers
