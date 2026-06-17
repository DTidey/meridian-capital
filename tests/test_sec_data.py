"""SEC EDGAR — Form 4 parsing, date cutoffs, cluster-buy detection."""

from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from data.db import insider_transactions
from data.sec_data import _flag_cluster_buys, _is_ceo_cfo, _parse_form4_xml

# ---------------------------------------------------------------------------
# Sample Form 4 XML (matches the element paths the parser uses)
# ---------------------------------------------------------------------------

FORM4_XML_PURCHASE = """\
<?xml version="1.0"?>
<ownershipDocument>
  <rptOwnerName>COOK TIMOTHY D</rptOwnerName>
  <officerTitle>Chief Executive Officer</officerTitle>
  <nonDerivativeTransaction>
    <transactionCoding>
      <transactionCode>P</transactionCode>
    </transactionCoding>
    <transactionShares><value>50000</value></transactionShares>
    <transactionPricePerShare><value>225.50</value></transactionPricePerShare>
    <transactionDate><value>2024-11-01</value></transactionDate>
    <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

FORM4_XML_SALE = """\
<?xml version="1.0"?>
<ownershipDocument>
  <rptOwnerName>JONES ALICE</rptOwnerName>
  <officerTitle>Vice President</officerTitle>
  <nonDerivativeTransaction>
    <transactionCoding>
      <transactionCode>S</transactionCode>
    </transactionCoding>
    <transactionShares><value>10000</value></transactionShares>
    <transactionPricePerShare><value>300.00</value></transactionPricePerShare>
    <transactionDate><value>2024-10-15</value></transactionDate>
    <directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

FORM4_XML_GRANT = """\
<?xml version="1.0"?>
<ownershipDocument>
  <rptOwnerName>SMITH BOB</rptOwnerName>
  <officerTitle>Director</officerTitle>
  <nonDerivativeTransaction>
    <transactionCoding>
      <transactionCode>A</transactionCode>
    </transactionCoding>
    <transactionShares><value>5000</value></transactionShares>
    <transactionPricePerShare><value>0.00</value></transactionPricePerShare>
    <transactionDate><value>2024-09-01</value></transactionDate>
    <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

FORM4_XML_MALFORMED = "this is not xml at all <<<"

FORM4_XML_MISSING_FIELDS = """\
<?xml version="1.0"?>
<ownershipDocument>
  <rptOwnerName>UNKNOWN</rptOwnerName>
  <nonDerivativeTransaction>
    <transactionCoding>
      <transactionCode>P</transactionCode>
    </transactionCoding>
  </nonDerivativeTransaction>
</ownershipDocument>
"""


# ---------------------------------------------------------------------------
# _is_ceo_cfo
# ---------------------------------------------------------------------------


class TestIsCeoCfo:
    @pytest.mark.parametrize(
        "title",
        [
            "Chief Executive Officer",
            "CEO",
            "ceo",
            "Chief Financial Officer",
            "CFO",
            "cfo",
            "chief executive",
            "Chief Financial",
            "Interim Chief Executive Officer",
        ],
    )
    def test_executive_titles_detected(self, title):
        assert _is_ceo_cfo(title) is True

    @pytest.mark.parametrize(
        "title",
        [
            "Vice President",
            "Director",
            "Senior Vice President",
            "General Counsel",
            "Chief Operating Officer",
            "Chief Technology Officer",
            "",
        ],
    )
    def test_non_executive_titles_not_detected(self, title):
        assert _is_ceo_cfo(title) is False

    def test_none_title(self):
        assert _is_ceo_cfo(None) is False


# ---------------------------------------------------------------------------
# _parse_form4_xml
# ---------------------------------------------------------------------------


class TestParseForm4Xml:
    def test_open_market_purchase(self):
        txns = _parse_form4_xml(FORM4_XML_PURCHASE)
        assert len(txns) == 1
        t = txns[0]
        assert t["insider_name"] == "COOK TIMOTHY D"
        assert t["insider_title"] == "Chief Executive Officer"
        assert t["transaction_code"] == "P"
        assert t["transaction_type"] == "Purchase"
        assert t["shares"] == pytest.approx(50_000)
        assert t["price"] == pytest.approx(225.50)
        assert t["date"] == "2024-11-01"
        assert t["ownership_type"] == "D"
        assert t["is_open_market"] == 1
        assert t["is_ceo_cfo"] == 1

    def test_sale_is_open_market(self):
        txns = _parse_form4_xml(FORM4_XML_SALE)
        assert len(txns) == 1
        t = txns[0]
        assert t["transaction_code"] == "S"
        assert t["transaction_type"] == "Sale"
        assert t["is_open_market"] == 1
        assert t["is_ceo_cfo"] == 0
        assert t["ownership_type"] == "I"

    def test_grant_not_open_market(self):
        txns = _parse_form4_xml(FORM4_XML_GRANT)
        assert len(txns) == 1
        t = txns[0]
        assert t["transaction_code"] == "A"
        assert t["transaction_type"] == "Grant"
        assert t["is_open_market"] == 0

    def test_malformed_xml_returns_empty(self):
        assert _parse_form4_xml(FORM4_XML_MALFORMED) == []

    def test_empty_string_returns_empty(self):
        assert _parse_form4_xml("") == []

    def test_missing_optional_fields_return_none(self):
        txns = _parse_form4_xml(FORM4_XML_MISSING_FIELDS)
        assert len(txns) == 1
        t = txns[0]
        assert t["shares"] is None
        assert t["price"] is None
        assert t["date"] is None

    def test_unknown_code_mapped_to_other(self):
        xml = FORM4_XML_PURCHASE.replace(
            "<transactionCode>P</transactionCode>", "<transactionCode>X</transactionCode>"
        )
        txns = _parse_form4_xml(xml)
        assert txns[0]["transaction_type"] == "Other"
        assert txns[0]["is_open_market"] == 0


# ---------------------------------------------------------------------------
# _flag_cluster_buys
# ---------------------------------------------------------------------------


def _insert_purchase(conn, ticker, insider_name, date_str, shares=1000):
    conn.execute(
        sa.insert(insider_transactions).values(
            ticker=ticker,
            insider_name=insider_name,
            insider_title="Director",
            transaction_type="Purchase",
            transaction_code="P",
            shares=shares,
            price=100.0,
            date=date_str,
            ownership_type="D",
            is_open_market=1,
            is_ceo_cfo=0,
            accession_no=f"acc-{insider_name}-{date_str}",
        )
    )


class TestFlagClusterBuys:
    def test_three_insiders_within_window_flagged(self, tmp_db, config):
        base = datetime.utcnow() - timedelta(days=10)
        for i, name in enumerate(["Alice", "Bob", "Charlie"]):
            _insert_purchase(tmp_db, "AAPL", name, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        tmp_db.commit()

        _flag_cluster_buys(tmp_db, "AAPL", config)

        flags = tmp_db.execute(
            sa.text("SELECT insider_count FROM insider_cluster_flags WHERE ticker='AAPL'")
        ).fetchall()
        assert any(f[0] >= 3 for f in flags)

    def test_two_insiders_below_threshold_not_flagged(self, tmp_db, config):
        base = datetime(2024, 10, 1)
        for name in ["Alice", "Bob"]:
            _insert_purchase(tmp_db, "TSLA", name, base.strftime("%Y-%m-%d"))
        tmp_db.commit()

        _flag_cluster_buys(tmp_db, "TSLA", config)

        flags = tmp_db.execute(
            sa.text("SELECT * FROM insider_cluster_flags WHERE ticker='TSLA'")
        ).fetchall()
        assert flags == []

    def test_same_insider_counted_once_per_window(self, tmp_db, config):
        base = datetime(2024, 10, 1)
        # Alice buys 3 times — should not count as 3 distinct insiders
        for i in range(3):
            _insert_purchase(
                tmp_db,
                "NVDA",
                "Alice",
                (base + timedelta(days=i)).strftime("%Y-%m-%d"),
                shares=1000 + i,
            )
        # Only Alice — need Bob and Charlie to hit threshold
        tmp_db.commit()
        _flag_cluster_buys(tmp_db, "NVDA", config)
        flags = tmp_db.execute(
            sa.text("SELECT * FROM insider_cluster_flags WHERE ticker='NVDA'")
        ).fetchall()
        assert flags == []

    def test_purchases_outside_lookback_ignored(self, tmp_db, config):
        lookback = config["sec"]["insider_lookback_days"]
        old_date = (datetime.utcnow() - timedelta(days=lookback + 10)).strftime("%Y-%m-%d")
        for name in ["Alice", "Bob", "Charlie"]:
            _insert_purchase(tmp_db, "META", name, old_date)
        tmp_db.commit()

        _flag_cluster_buys(tmp_db, "META", config)

        flags = tmp_db.execute(
            sa.text("SELECT * FROM insider_cluster_flags WHERE ticker='META'")
        ).fetchall()
        assert flags == []

    def test_insiders_outside_window_not_grouped(self, tmp_db, config):
        window = config["sec"]["cluster_buy_window_days"]
        # Space purchases > window apart so they can't be in the same window
        _dates = [
            datetime(2024, 1, 1),
            datetime(2024, 1, 1) + timedelta(days=window + 5),
            datetime(2024, 1, 1) + timedelta(days=window * 2 + 10),
        ]
        # All within lookback
        _recent_dates = [
            (datetime.utcnow() - timedelta(days=30 - i * 2)).strftime("%Y-%m-%d") for i in range(3)
        ]
        # Use recent dates that ARE within lookback but spaced > window apart
        spaced_dates = [
            (datetime.utcnow() - timedelta(days=i * (window + 5))).strftime("%Y-%m-%d")
            for i in range(3)
        ]
        # Only the most recent two will be within lookback
        for name, date in zip(["Alice", "Bob", "Charlie"], spaced_dates, strict=False):
            _insert_purchase(tmp_db, "AMZN", name, date)
        tmp_db.commit()

        _flag_cluster_buys(tmp_db, "AMZN", config)
        # Each is in its own window — no cluster
        flags = tmp_db.execute(
            sa.text("SELECT insider_count FROM insider_cluster_flags WHERE ticker='AMZN'")
        ).fetchall()
        assert all(f[0] < 3 for f in flags)
