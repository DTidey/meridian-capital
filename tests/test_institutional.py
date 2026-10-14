"""Institutional holdings — 13-F XML parsing, prior-quarter dates, summary rebuild."""

from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa
import yaml

from data import institutional as inst
from data.db import insert_or_ignore, institutional_holdings
from data.institutional import (
    _aggregate_by_ticker,
    _get_recent_13f_accessions,
    _infotable_filenames,
    _normalise_value_units,
    _parse_infotable,
    _prior_quarter_report_date,
    _purge_legacy_rows,
    _RateLimiter,
    _summarise_holdings,
    update_institutional,
)

# ---------------------------------------------------------------------------
# Sample infotable XML (matches the parser's namespace handling)
# ---------------------------------------------------------------------------

INFOTABLE_XML_WITH_NS = """\
<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>1500000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>5000000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <issuerTicker>AAPL</issuerTicker>
  </infoTable>
  <infoTable>
    <nameOfIssuer>MICROSOFT CORP</nameOfIssuer>
    <cusip>594918104</cusip>
    <value>800000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>2000000</sshPrnamt>
    </shrsOrPrnAmt>
    <issuerTicker>MSFT</issuerTicker>
  </infoTable>
</informationTable>
"""

INFOTABLE_XML_NO_NS = """\
<?xml version="1.0"?>
<informationTable>
  <infoTable>
    <nameOfIssuer>NVIDIA CORP</nameOfIssuer>
    <value>2000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>1000000</sshPrnamt>
    </shrsOrPrnAmt>
    <issuerTicker>NVDA</issuerTicker>
  </infoTable>
</informationTable>
"""

INFOTABLE_XML_MISSING_TICKER = """\
<?xml version="1.0"?>
<informationTable>
  <infoTable>
    <nameOfIssuer>SOME COMPANY</nameOfIssuer>
    <value>100000</value>
    <shrsOrPrnAmt><sshPrnamt>10000</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""

INFOTABLE_XML_COMMA_NUMBERS = """\
<?xml version="1.0"?>
<informationTable>
  <infoTable>
    <value>1,500,000</value>
    <shrsOrPrnAmt><sshPrnamt>5,000,000</sshPrnamt></shrsOrPrnAmt>
    <issuerTicker>AAPL</issuerTicker>
  </infoTable>
</informationTable>
"""


# ---------------------------------------------------------------------------
# _parse_infotable
# ---------------------------------------------------------------------------


class TestParseInfotable:
    def test_parses_namespaced_xml(self):
        holdings = _parse_infotable(INFOTABLE_XML_WITH_NS)
        assert len(holdings) == 2
        aapl = next(h for h in holdings if h["ticker"] == "AAPL")
        assert aapl["shares"] == pytest.approx(5_000_000)
        assert aapl["market_value"] == pytest.approx(1_500_000)

    def test_parses_no_namespace(self):
        holdings = _parse_infotable(INFOTABLE_XML_NO_NS)
        assert len(holdings) == 1
        assert holdings[0]["ticker"] == "NVDA"
        assert holdings[0]["shares"] == pytest.approx(1_000_000)

    def test_skips_rows_without_ticker(self):
        holdings = _parse_infotable(INFOTABLE_XML_MISSING_TICKER)
        assert holdings == []

    def test_handles_comma_formatted_numbers(self):
        holdings = _parse_infotable(INFOTABLE_XML_COMMA_NUMBERS)
        assert len(holdings) == 1
        assert holdings[0]["shares"] == pytest.approx(5_000_000)
        assert holdings[0]["market_value"] == pytest.approx(1_500_000)

    def test_malformed_xml_returns_empty(self):
        assert _parse_infotable("not xml at all <<<") == []

    def test_ticker_uppercased(self):
        xml = INFOTABLE_XML_NO_NS.replace(
            "<issuerTicker>NVDA</issuerTicker>", "<issuerTicker>nvda</issuerTicker>"
        )
        holdings = _parse_infotable(xml)
        assert holdings[0]["ticker"] == "NVDA"

    def test_multiple_holdings_parsed(self):
        holdings = _parse_infotable(INFOTABLE_XML_WITH_NS)
        tickers = {h["ticker"] for h in holdings}
        assert tickers == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# _prior_quarter_report_date
# ---------------------------------------------------------------------------


class TestPriorQuarterReportDate:
    @pytest.mark.parametrize(
        "date,expected",
        [
            ("2024-06-30", "2024-03-30"),  # Q2 → Q1
            ("2024-09-30", "2024-06-30"),  # Q3 → Q2
            ("2024-12-31", "2024-09-31"),  # Q4 → Q3  (day preserved even if invalid)
            ("2024-03-31", "2023-12-31"),  # Q1 → Q4 prior year
            ("2024-01-15", "2023-10-15"),  # Jan → Oct prior year
            ("2024-02-28", "2023-11-28"),  # Feb → Nov prior year
        ],
    )
    def test_quarter_subtraction(self, date, expected):
        assert _prior_quarter_report_date(date) == expected

    def test_invalid_date_returns_none(self):
        assert _prior_quarter_report_date("not-a-date") is None

    def test_none_input_returns_none(self):
        assert _prior_quarter_report_date(None) is None


# ---------------------------------------------------------------------------
# _summarise_holdings
# ---------------------------------------------------------------------------


def _insert_holding(conn, fund, ticker, shares, report_date, filed_date="2024-11-14"):
    conn.execute(
        insert_or_ignore(conn, institutional_holdings).values(
            fund_name=fund,
            ticker=ticker,
            shares_held=shares,
            market_value=shares * 100,
            report_date=report_date,
            filed_date=filed_date,
        )
    )


class TestSummariseHoldings:
    def test_counts_funds_per_ticker(self, tmp_db):
        for fund in ["Citadel", "Point72", "Bridgewater"]:
            _insert_holding(tmp_db, fund, "AAPL", 1_000_000, "2024-09-30")
        tmp_db.commit()

        _summarise_holdings(tmp_db)

        row = tmp_db.execute(
            sa.text("SELECT funds_holding FROM institutional_summary WHERE ticker='AAPL'")
        ).fetchone()
        assert row[0] == 3

    def test_net_share_change_computed(self, tmp_db):
        # Prior quarter (approx 3 months back)
        _insert_holding(tmp_db, "Citadel", "MSFT", 1_000_000, "2024-06-30")
        # Current quarter
        _insert_holding(tmp_db, "Citadel", "MSFT", 1_200_000, "2024-09-30")
        tmp_db.commit()

        _summarise_holdings(tmp_db)

        row = tmp_db.execute(
            sa.text(
                "SELECT net_share_change FROM institutional_summary "
                "WHERE ticker='MSFT' AND report_date='2024-09-30'"
            )
        ).fetchone()
        assert row[0] == pytest.approx(200_000)

    def test_new_position_detected(self, tmp_db):
        # Fund holds NVDA in Q3 but not in its Q2 filing → new position
        _insert_holding(tmp_db, "Tiger", "AAPL", 100_000, "2024-06-30")
        _insert_holding(tmp_db, "Tiger", "NVDA", 500_000, "2024-09-30")
        tmp_db.commit()

        _summarise_holdings(tmp_db)

        row = tmp_db.execute(
            sa.text(
                "SELECT new_positions FROM institutional_summary "
                "WHERE ticker='NVDA' AND report_date='2024-09-30'"
            )
        ).fetchone()
        assert row[0] == 1

    def test_summary_rebuilt_on_repeat_call(self, tmp_db):
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        # Add another fund and rebuild
        _insert_holding(tmp_db, "Point72", "AAPL", 500_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        row = tmp_db.execute(
            sa.text("SELECT funds_holding FROM institutional_summary WHERE ticker='AAPL'")
        ).fetchone()
        assert row[0] == 2

    def test_no_prior_quarter_net_change_is_none(self, tmp_db):
        _insert_holding(tmp_db, "Baupost", "TSLA", 300_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        row = tmp_db.execute(
            sa.text("SELECT net_share_change FROM institutional_summary WHERE ticker='TSLA'")
        ).fetchone()
        assert row[0] is None

    def test_fund_not_yet_filed_carries_forward(self, tmp_db):
        # Citadel filed Q3; Point72 has only filed Q2 so far (mid filing season)
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000_000, "2024-06-30")
        _insert_holding(tmp_db, "Point72", "AAPL", 2_000_000, "2024-06-30")
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_100_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        row = _summary_row(tmp_db, "AAPL", "2024-09-30")
        assert row["funds_holding"] == 2
        # Point72 hasn't filed Q3, so it must not read as selling 2M shares
        assert row["net_share_change"] == pytest.approx(100_000)
        assert row["new_positions"] == 0

    def test_full_exit_row_written(self, tmp_db):
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000_000, "2024-06-30")
        _insert_holding(tmp_db, "Citadel", "MSFT", 500_000, "2024-06-30")
        _insert_holding(tmp_db, "Citadel", "MSFT", 500_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        row = _summary_row(tmp_db, "AAPL", "2024-09-30")
        assert row["funds_holding"] == 0
        assert row["net_share_change"] == pytest.approx(-1_000_000)

    def test_first_filing_has_no_new_positions(self, tmp_db):
        _insert_holding(tmp_db, "Baupost", "TSLA", 300_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        assert _summary_row(tmp_db, "TSLA", "2024-09-30")["new_positions"] == 0

    def test_unheld_ticker_gets_zero_change_when_comparison_exists(self, tmp_db):
        # Citadel compares Q3 with Q2; Point72 (first filing) holds NVDA, Citadel never did
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000_000, "2024-06-30")
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000_000, "2024-09-30")
        _insert_holding(tmp_db, "Point72", "NVDA", 400_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        row = _summary_row(tmp_db, "NVDA", "2024-09-30")
        assert row["funds_holding"] == 1
        assert row["net_share_change"] == pytest.approx(0.0)
        assert row["new_positions"] == 0

    def test_skipped_quarter_compares_with_latest_earlier_filing(self, tmp_db):
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000_000, "2024-03-31")
        _insert_holding(tmp_db, "Point72", "MSFT", 1, "2024-06-30")
        _insert_holding(tmp_db, "Citadel", "AAPL", 1_500_000, "2024-09-30")
        tmp_db.commit()
        _summarise_holdings(tmp_db)

        assert _summary_row(tmp_db, "AAPL", "2024-09-30")["net_share_change"] == pytest.approx(
            500_000
        )


def _summary_row(conn, ticker, report_date):
    row = conn.execute(
        sa.text(
            "SELECT funds_holding, net_share_change, new_positions FROM institutional_summary "
            "WHERE ticker=:t AND report_date=:d"
        ),
        {"t": ticker, "d": report_date},
    ).fetchone()
    assert row is not None, f"no summary row for {ticker} {report_date}"
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# AC1, AC2 — value units, options and principal amounts
# ---------------------------------------------------------------------------


def _infotable(*entries: str) -> str:
    return f"<informationTable>{''.join(entries)}</informationTable>"


def _entry(ticker, shares, value, put_call="", amount_type="SH"):
    pc = f"<putCall>{put_call}</putCall>" if put_call is not None else ""
    at = f"<sshPrnamtType>{amount_type}</sshPrnamtType>" if amount_type else ""
    return (
        f"<infoTable><issuerTicker>{ticker}</issuerTicker><value>{value}</value>"
        f"<shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt>{at}</shrsOrPrnAmt>{pc}</infoTable>"
    )


class TestParseInfotableRules:
    def test_value_is_whole_dollars(self):
        rows = _parse_infotable(_infotable(_entry("AAPL", 491200, 142133632)))
        assert rows[0]["market_value"] == pytest.approx(142_133_632)

    def test_skips_options(self):
        xml = _infotable(
            _entry("AAPL", 491200, 142133632, put_call="Call"),
            _entry("AAPL", 375000, 108510000, put_call="Put"),
            _entry("AAPL", 1000, 289000),
        )
        rows = _parse_infotable(xml)
        assert len(rows) == 1
        assert rows[0]["shares"] == pytest.approx(1000)

    def test_blank_put_call_is_common_stock(self):
        # Adversarial: an empty <putCall/> element is not an option
        rows = _parse_infotable(_infotable(_entry("AAPL", 1000, 289000, put_call="  ")))
        assert len(rows) == 1

    def test_skips_principal_amounts(self):
        rows = _parse_infotable(_infotable(_entry("AAPL", 5_000_000, 4_900_000, amount_type="PRN")))
        assert rows == []

    def test_missing_amount_type_is_kept(self):
        rows = _parse_infotable(_infotable(_entry("AAPL", 1000, 289000, amount_type="")))
        assert len(rows) == 1

    def test_put_call_case_insensitive_namespaced(self):
        # Adversarial: real filings are namespaced and may use upper case
        xml = (
            '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/'
            'informationtable">' + _entry("AAPL", 10, 2890, put_call="CALL") + "</informationTable>"
        )
        assert _parse_infotable(xml) == []


# ---------------------------------------------------------------------------
# AC3 — aggregation per ticker within a filing
# ---------------------------------------------------------------------------


class TestAggregateByTicker:
    def test_sums_rows_for_same_ticker(self):
        rows = [
            {"ticker": "AAPL", "shares": 100.0, "market_value": 1000.0},
            {"ticker": "AAPL", "shares": 50.0, "market_value": 500.0},
            {"ticker": "MSFT", "shares": 10.0, "market_value": 4000.0},
        ]
        out = {r["ticker"]: r for r in _aggregate_by_ticker(rows)}
        assert out["AAPL"]["shares"] == pytest.approx(150)
        assert out["AAPL"]["market_value"] == pytest.approx(1500)
        assert out["MSFT"]["shares"] == pytest.approx(10)

    def test_none_counts_as_zero_when_others_present(self):
        rows = [
            {"ticker": "AAPL", "shares": None, "market_value": 1000.0},
            {"ticker": "AAPL", "shares": 50.0, "market_value": None},
        ]
        (out,) = _aggregate_by_ticker(rows)
        assert out["shares"] == pytest.approx(50)
        assert out["market_value"] == pytest.approx(1000)

    def test_all_none_stays_none(self):
        (out,) = _aggregate_by_ticker([{"ticker": "AAPL", "shares": None, "market_value": None}])
        assert out["shares"] is None
        assert out["market_value"] is None

    def test_empty_input(self):
        assert _aggregate_by_ticker([]) == []


# ---------------------------------------------------------------------------
# AC4 — filing selection
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload=None, ok=True):
        self._payload = payload
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        resp = self.responses.get(url)
        if isinstance(resp, Exception):
            raise resp
        return resp or _Resp(ok=False)


def _submissions(*filings):
    forms, filed, accs, periods = zip(*filings, strict=True) if filings else ([], [], [], [])
    return {
        "filings": {
            "recent": {
                "form": list(forms),
                "filingDate": list(filed),
                "accessionNumber": list(accs),
                "reportDate": list(periods),
            }
        }
    }


_SUBS_URL = "https://data.sec.gov/submissions/CIK0001423053.json"


class TestRecentAccessions:
    def _call(self, payload, max_filings=6):
        session = _Session({_SUBS_URL: _Resp(payload)})
        return _get_recent_13f_accessions(session, "0001423053", _RateLimiter(0), max_filings)

    def test_returns_period_and_skips_amendments(self):
        payload = _submissions(
            ("13F-HR/A", "2026-09-02", "A3", "2026-06-30"),
            ("13F-HR", "2026-08-14", "A2", "2026-06-30"),
            ("4", "2026-08-01", "X", ""),
            ("13F-NT", "2026-05-20", "N1", "2026-03-31"),
            ("13F-HR", "2026-05-15", "A1", "2026-03-31"),
        )
        assert self._call(payload) == [
            ("A2", "2026-08-14", "2026-06-30"),
            ("A1", "2026-05-15", "2026-03-31"),
        ]

    def test_duplicate_period_keeps_most_recent_filing(self):
        payload = _submissions(
            ("13F-HR", "2026-08-20", "NEW", "2026-06-30"),
            ("13F-HR", "2026-08-14", "OLD", "2026-06-30"),
        )
        assert self._call(payload) == [("NEW", "2026-08-20", "2026-06-30")]

    def test_max_filings_counts_distinct_periods(self):
        payload = _submissions(
            ("13F-HR", "2026-08-20", "A3", "2026-06-30"),
            ("13F-HR", "2026-08-14", "A2", "2026-06-30"),
            ("13F-HR", "2026-05-15", "A1", "2026-03-31"),
            ("13F-HR", "2026-02-14", "A0", "2025-12-31"),
        )
        assert [a for a, _, _ in self._call(payload, max_filings=2)] == ["A3", "A1"]

    def test_missing_report_date_skipped(self):
        # Adversarial: a 13F-HR without a period can't be placed in a quarter
        payload = _submissions(("13F-HR", "2026-08-14", "A1", ""))
        assert self._call(payload) == []

    def test_order_enforced_not_assumed(self):
        # Adversarial: if SEC returned oldest first, the newer filing must still win
        payload = _submissions(
            ("13F-HR", "2026-08-14", "OLD", "2026-06-30"),
            ("13F-HR", "2026-08-20", "NEW", "2026-06-30"),
        )
        assert self._call(payload) == [("NEW", "2026-08-20", "2026-06-30")]

    def test_fetch_failure_returns_empty(self):
        session = _Session({})
        assert _get_recent_13f_accessions(session, "0001423053", _RateLimiter(0)) == []


# ---------------------------------------------------------------------------
# AC7 — infotable discovery
# ---------------------------------------------------------------------------

_BASE = "https://www.sec.gov/Archives/edgar/data/1061768/000106176826000010"


def _index(*names):
    return {"directory": {"item": [{"name": n} for n in names]}}


class TestInfotableFilenames:
    def test_lists_non_primary_xml(self):
        session = _Session(
            {
                f"{_BASE}/index.json": _Resp(
                    _index("x-index.html", "x.txt", "BGLLCQ22026.xml", "primary_doc.xml")
                )
            }
        )
        assert _infotable_filenames(session, _BASE, _RateLimiter(0)) == ["BGLLCQ22026.xml"]

    def test_falls_back_when_index_fails(self):
        session = _Session({})
        assert _infotable_filenames(session, _BASE, _RateLimiter(0)) == [
            "infotable.xml",
            "form13fInfoTable.xml",
        ]

    def test_falls_back_when_index_lists_no_xml(self):
        session = _Session({f"{_BASE}/index.json": _Resp(_index("primary_doc.xml", "a.txt"))})
        assert _infotable_filenames(session, _BASE, _RateLimiter(0)) == [
            "infotable.xml",
            "form13fInfoTable.xml",
        ]

    def test_rejects_path_like_names(self):
        # Adversarial: a crafted index must not redirect requests outside the filing folder
        session = _Session(
            {f"{_BASE}/index.json": _Resp(_index("../../evil.xml", "sub/x.xml", "ok.xml"))}
        )
        assert _infotable_filenames(session, _BASE, _RateLimiter(0)) == ["ok.xml"]

    def test_rejects_query_fragment_and_backslash(self):
        # Adversarial: only plain filenames reach the request URL
        session = _Session(
            {f"{_BASE}/index.json": _Resp(_index("a.xml?x=1", "b#.xml", "c\\d.xml", "ok-1.XML"))}
        )
        assert _infotable_filenames(session, _BASE, _RateLimiter(0)) == ["ok-1.XML"]

    def test_malformed_index_falls_back(self):
        session = _Session({f"{_BASE}/index.json": _Resp({"unexpected": True})})
        assert _infotable_filenames(session, _BASE, _RateLimiter(0))[0] == "infotable.xml"


# ---------------------------------------------------------------------------
# AC5 — storage and legacy purge
# ---------------------------------------------------------------------------


def _cfg(*funds):
    return {
        "institutional": {
            "tracked_funds": [{"name": n, "cik": c} for n, c in funds],
            "new_position_flag_count": 3,
        }
    }


class TestStorage:
    def test_stores_period_and_filed_date_aggregated(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            inst,
            "_get_recent_13f_accessions",
            lambda *a, **k: [("A1", "2026-08-14", "2026-06-30")],
        )
        monkeypatch.setattr(
            inst,
            "_fetch_13f_infotable",
            lambda *a, **k: [
                {"ticker": "AAPL", "shares": 100.0, "market_value": 29_000.0},
                {"ticker": "AAPL", "shares": 50.0, "market_value": 14_500.0},
            ],
        )
        update_institutional(tmp_db, _cfg(("Point72", "1")))

        rows = tmp_db.execute(
            sa.select(
                institutional_holdings.c.ticker,
                institutional_holdings.c.report_date,
                institutional_holdings.c.filed_date,
                institutional_holdings.c.shares_held,
                institutional_holdings.c.market_value,
            )
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][:3] == ("AAPL", "2026-06-30", "2026-08-14")
        assert rows[0][3] == pytest.approx(150)
        assert rows[0][4] == pytest.approx(43_500)

    def test_thousands_filing_rescaled_and_logged(self, tmp_db, monkeypatch, caplog):
        monkeypatch.setattr(
            inst,
            "_get_recent_13f_accessions",
            lambda *a, **k: [("A1", "2026-08-13", "2026-06-30")],
        )
        monkeypatch.setattr(
            inst,
            "_fetch_13f_infotable",
            lambda *a, **k: [{"ticker": "AMZN", "shares": 3_743_854.0, "market_value": 892_310.0}],
        )
        with caplog.at_level("INFO", logger="data.institutional"):
            update_institutional(tmp_db, _cfg(("Baupost", "1")))

        value = tmp_db.execute(sa.select(institutional_holdings.c.market_value)).scalar()
        assert value == pytest.approx(892_310_000)
        assert "reported in $ thousands" in caplog.text

    def test_purges_legacy_rows_before_fetch(self, tmp_db, monkeypatch, caplog):
        _insert_holding(tmp_db, "Point72", "AAPL", 491_200, "2026-08-14", filed_date=None)
        tmp_db.commit()
        monkeypatch.setattr(inst, "_get_recent_13f_accessions", lambda *a, **k: [])

        update_institutional(tmp_db, _cfg(("Point72", "1")))

        assert "Removed 1 legacy 13-F rows" in caplog.text
        count = tmp_db.execute(
            sa.select(sa.func.count()).select_from(institutional_holdings)
        ).scalar()
        assert count == 0


class TestPurgeLegacyRows:
    def test_deletes_only_null_filed_date(self, tmp_db):
        _insert_holding(tmp_db, "Citadel", "AAPL", 1, "2026-08-14", filed_date=None)
        _insert_holding(tmp_db, "Citadel", "MSFT", 1, "2026-08-14", filed_date=None)
        _insert_holding(tmp_db, "Citadel", "AAPL", 1, "2026-06-30", filed_date="2026-08-14")
        tmp_db.commit()

        assert _purge_legacy_rows(tmp_db) == 2
        remaining = tmp_db.execute(sa.select(institutional_holdings.c.ticker)).scalars().all()
        assert remaining == ["AAPL"]

    def test_noop_when_nothing_legacy(self, tmp_db):
        assert _purge_legacy_rows(tmp_db) == 0


# ---------------------------------------------------------------------------
# AC8 — config CIKs
# ---------------------------------------------------------------------------


class TestConfigCiks:
    def test_third_point_and_appaloosa_ciks(self):
        cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
        ciks = {f["name"]: f["cik"] for f in cfg["institutional"]["tracked_funds"]}
        assert ciks["Third Point"] == "0001040273"
        assert ciks["Appaloosa Management"] == "0001656456"


# ---------------------------------------------------------------------------
# AC9 — filings still reported in $ thousands
# ---------------------------------------------------------------------------


class TestNormaliseValueUnits:
    def test_thousands_filing_rescaled(self):
        # Baupost Q2 2026: AMZN 3,743,854 shares reported as 892,310
        rows = [
            {"ticker": "AMZN", "shares": 3_743_854.0, "market_value": 892_310.0},
            {"ticker": "GOOGL", "shares": 1_371_931.0, "market_value": 484_744.0},
        ]
        assert _normalise_value_units(rows) is True
        assert rows[0]["market_value"] == pytest.approx(892_310_000)
        assert rows[1]["market_value"] == pytest.approx(484_744_000)

    def test_dollar_filing_unchanged(self):
        rows = [{"ticker": "AAPL", "shares": 7_296_076.0, "market_value": 2_111_200_000.0}]
        assert _normalise_value_units(rows) is False
        assert rows[0]["market_value"] == pytest.approx(2_111_200_000)

    def test_one_odd_row_does_not_flip_filing(self):
        # Adversarial: a single bad row must not rescale a dollar-valued filing
        rows = [
            {"ticker": "AAPL", "shares": 100.0, "market_value": 28_936.0},
            {"ticker": "MSFT", "shares": 100.0, "market_value": 45_000.0},
            {"ticker": "BAD", "shares": 1_000_000.0, "market_value": 5.0},
        ]
        assert _normalise_value_units(rows) is False
        assert rows[2]["market_value"] == pytest.approx(5.0)

    def test_rows_without_shares_or_value_ignored(self):
        # Adversarial: None and zero values can't divide or decide units
        rows = [
            {"ticker": "AAPL", "shares": None, "market_value": 500.0},
            {"ticker": "MSFT", "shares": 0.0, "market_value": 500.0},
            {"ticker": "NVDA", "shares": 100.0, "market_value": None},
        ]
        assert _normalise_value_units(rows) is False
        assert rows[0]["market_value"] == pytest.approx(500.0)
        assert rows[2]["market_value"] is None

    def test_empty_filing(self):
        assert _normalise_value_units([]) is False


# ---------------------------------------------------------------------------
# AC10 — scoring loader is point-in-time by filing date
# ---------------------------------------------------------------------------


class TestLoaderPointInTime:
    def test_filings_after_cutoff_excluded(self, tmp_db):
        from datetime import date

        from factors.loader import _load_institutional

        _insert_holding(tmp_db, "Citadel", "AAPL", 1_000, "2026-03-31", filed_date="2026-05-15")
        _insert_holding(tmp_db, "Citadel", "AAPL", 9_000, "2026-06-30", filed_date="2026-08-14")
        tmp_db.commit()

        # 2026-07-31 is after the Q2 quarter end but before the Q2 filing
        df = _load_institutional(tmp_db, date(2026, 7, 31))
        assert df["report_date"].max() == pd.Timestamp("2026-03-31")

        df = _load_institutional(tmp_db, date(2026, 8, 14))
        latest = df.sort_values("report_date").iloc[-1]
        assert latest["report_date"] == pd.Timestamp("2026-06-30")
        assert latest["net_share_change"] == pytest.approx(8_000)

    def test_empty_holdings_gives_empty_frame(self, tmp_db):
        from datetime import date

        from factors.loader import _load_institutional

        df = _load_institutional(tmp_db, date(2026, 8, 14))
        assert df.empty
        assert "funds_holding" in df.columns
