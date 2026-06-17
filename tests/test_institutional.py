"""Institutional holdings — 13-F XML parsing, prior-quarter dates, summary rebuild."""

import pytest
import sqlalchemy as sa

from data.db import insert_or_ignore, institutional_holdings
from data.institutional import _parse_infotable, _prior_quarter_report_date, _summarise_holdings

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
        # value reported in $thousands: 1_500_000 * 1000
        assert aapl["market_value"] == pytest.approx(1_500_000_000)

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
        assert holdings[0]["market_value"] == pytest.approx(1_500_000_000)

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


def _insert_holding(conn, fund, ticker, shares, report_date):
    conn.execute(
        insert_or_ignore(conn, institutional_holdings).values(
            fund_name=fund,
            ticker=ticker,
            shares_held=shares,
            market_value=shares * 100,
            report_date=report_date,
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
        # Fund holds in Q3 but not Q2 → new position
        _insert_holding(tmp_db, "Tiger", "NVDA", 500_000, "2024-09-30")
        # No prior quarter row for Tiger/NVDA
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
