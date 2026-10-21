"""SEC EDGAR — Form 4 parsing, date cutoffs, cluster-buy detection."""

import logging
from datetime import datetime, timedelta

import pytest
import requests
import sqlalchemy as sa

from data import sec_data as sec
from data._utils import utc_now
from data.db import insider_cluster_flags, insider_transactions, sec_filings
from data.sec_data import (
    _flag_cluster_buys,
    _is_ceo_cfo,
    _load_cik_map,
    _parse_form4_xml,
    rebuild_insider_transactions,
    update_sec_data,
)

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
        _, txns = _parse_form4_xml(FORM4_XML_PURCHASE)
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
        _, txns = _parse_form4_xml(FORM4_XML_SALE)
        assert len(txns) == 1
        t = txns[0]
        assert t["transaction_code"] == "S"
        assert t["transaction_type"] == "Sale"
        assert t["is_open_market"] == 1
        assert t["is_ceo_cfo"] == 0
        assert t["ownership_type"] == "I"

    def test_grant_not_open_market(self):
        _, txns = _parse_form4_xml(FORM4_XML_GRANT)
        assert len(txns) == 1
        t = txns[0]
        assert t["transaction_code"] == "A"
        assert t["transaction_type"] == "Grant"
        assert t["is_open_market"] == 0

    def test_malformed_xml_returns_empty(self):
        assert _parse_form4_xml(FORM4_XML_MALFORMED) == (None, [])

    def test_empty_string_returns_empty(self):
        assert _parse_form4_xml("") == (None, [])

    def test_missing_optional_fields_return_none(self):
        _, txns = _parse_form4_xml(FORM4_XML_MISSING_FIELDS)
        assert len(txns) == 1
        t = txns[0]
        assert t["shares"] is None
        assert t["price"] is None
        assert t["date"] is None

    def test_unknown_code_mapped_to_other(self):
        xml = FORM4_XML_PURCHASE.replace(
            "<transactionCode>P</transactionCode>", "<transactionCode>X</transactionCode>"
        )
        _, txns = _parse_form4_xml(xml)
        assert txns[0]["transaction_type"] == "Other"
        assert txns[0]["is_open_market"] == 0

    # AC4 (packet 39): issuer CIK and position in the filing
    def test_issuer_cik_and_txn_index(self):
        xml = _form4(
            issuer_cik=AAPL_CIK,
            txns=[("M", 43778, "2026-10-01"), ("S", 43778, "2026-10-01"), ("F", 5, "2026-10-01")],
        )
        issuer, txns = _parse_form4_xml(xml)
        assert issuer == AAPL_CIK
        assert [t["txn_index"] for t in txns] == [0, 1, 2]
        assert [t["transaction_code"] for t in txns] == ["M", "S", "F"]

    def test_leading_zeros_stripped(self):
        issuer, _ = _parse_form4_xml(_form4(issuer_cik="0001067983"))
        assert issuer == "1067983"

    def test_missing_issuer_cik_is_none(self):
        issuer, txns = _parse_form4_xml(_form4(issuer_cik=None))
        assert issuer is None
        assert len(txns) == 1

    def test_derivative_only_has_no_rows(self):
        xml = _form4(txns=[]).replace(
            "<nonDerivativeTable>",
            "<derivativeTable><derivativeTransaction/></derivativeTable><nonDerivativeTable>",
        )
        issuer, txns = _parse_form4_xml(xml)
        assert issuer == AAPL_CIK
        assert txns == []

    # Adversarial: a non-numeric issuer CIK is not trusted.
    def test_non_digit_issuer_cik_is_none(self):
        issuer, _ = _parse_form4_xml(_form4(issuer_cik="320193 OR 1=1"))
        assert issuer is None


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
        base = utc_now() - timedelta(days=10)
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
        old_date = (utc_now() - timedelta(days=lookback + 10)).strftime("%Y-%m-%d")
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
            (utc_now() - timedelta(days=30 - i * 2)).strftime("%Y-%m-%d") for i in range(3)
        ]
        # Use recent dates that ARE within lookback but spaced > window apart
        spaced_dates = [
            (utc_now() - timedelta(days=i * (window + 5))).strftime("%Y-%m-%d") for i in range(3)
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


# ---------------------------------------------------------------------------
# Packet 39: issuer integrity — helpers
# ---------------------------------------------------------------------------

AAPL_CIK = "320193"
LENNAR_CIK = "920760"
ALPHABET_CIK = "1652044"


def _today() -> str:
    return utc_now().strftime("%Y-%m-%d")


def _form4(issuer_cik=AAPL_CIK, owner="COOK TIMOTHY D", title="Director", txns=None, symbol=None):
    """Form 4 ownershipDocument; txns is a list of (code, shares, date)."""
    txns = txns if txns is not None else [("P", 1000, _today())]
    issuer = f"<issuerCik>{issuer_cik}</issuerCik>" if issuer_cik is not None else ""
    if symbol is not None:
        issuer += f"<issuerTradingSymbol>{symbol}</issuerTradingSymbol>"
    body = "".join(
        f"""
    <nonDerivativeTransaction>
      <transactionDate><value>{date}</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>10.00</value></transactionPricePerShare>
      </transactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>"""
        for code, shares, date in txns
    )
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer>{issuer}<issuerName>Issuer</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><officerTitle>{title}</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>{body}
  </nonDerivativeTable>
</ownershipDocument>"""


class _Resp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self.ok = status < 400
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class _FakeSession:
    """Routes GET calls by URL substring; records every URL requested."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.urls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return _Resp(404)


def _tickers_json(mapping: dict) -> dict:
    return {
        str(i): {"cik_str": cik, "ticker": t, "title": t}
        for i, (t, cik) in enumerate(mapping.items())
    }


def _subs(accessions: list[str], form="4") -> dict:
    return {
        "filings": {
            "recent": {
                "form": [form] * len(accessions),
                "filingDate": [_today()] * len(accessions),
                "accessionNumber": accessions,
                "primaryDocument": ["doc.xml"] * len(accessions),
            }
        }
    }


def _sgml(xml: str) -> str:
    return f"<SEC-DOCUMENT>\n<TEXT>\n<XML>\n{xml}\n</XML>\n</TEXT>\n</SEC-DOCUMENT>"


@pytest.fixture
def fake_sec(monkeypatch):
    """Install a fake requests session in sec_data; no rate-limit sleeps."""
    monkeypatch.setattr(sec, "_MIN_INTERVAL", 0.0)

    def install(routes: dict) -> _FakeSession:
        session = _FakeSession(routes)
        monkeypatch.setattr(sec, "_make_session", lambda config: session)
        return session

    return install


def _rows(conn, ticker=None):
    q = sa.select(
        insider_transactions.c.ticker,
        insider_transactions.c.accession_no,
        insider_transactions.c.txn_index,
        insider_transactions.c.transaction_code,
        insider_transactions.c.shares,
    ).order_by(
        insider_transactions.c.ticker,
        insider_transactions.c.accession_no,
        insider_transactions.c.txn_index,
    )
    if ticker:
        q = q.where(insider_transactions.c.ticker == ticker)
    return [tuple(r) for r in conn.execute(q).fetchall()]


# ---------------------------------------------------------------------------
# AC1: CIK map
# ---------------------------------------------------------------------------


class TestCikMap:
    def test_maps_tickers_and_strips_zeros(self):
        payload = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": "0001067983", "ticker": "BRK-B", "title": "Berkshire"},
            "2": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet"},
            "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet"},
        }
        session = _FakeSession({"company_tickers.json": _Resp(json_data=payload)})
        cik_map = _load_cik_map(session, sec._RateLimiter(0))
        assert cik_map == {
            "AAPL": "320193",
            "BRK-B": "1067983",
            "GOOG": "1652044",
            "GOOGL": "1652044",
        }
        assert session.urls == ["https://www.sec.gov/files/company_tickers.json"]

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {},
            ["AAPL", 320193],
            {"0": "AAPL"},
            {"0": {"ticker": "AAPL"}},
            {"0": {"cik_str": 320193}},
            {"0": {"cik_str": None, "ticker": "AAPL"}},
        ],
    )
    def test_unexpected_json_raises(self, payload):
        session = _FakeSession({"company_tickers.json": _Resp(json_data=payload)})
        with pytest.raises(ValueError):
            _load_cik_map(session, sec._RateLimiter(0))

    def test_invalid_json_raises(self):
        session = _FakeSession({"company_tickers.json": _Resp(json_data=ValueError("bad json"))})
        with pytest.raises(ValueError):
            _load_cik_map(session, sec._RateLimiter(0))

    def test_http_error_raises(self):
        session = _FakeSession({"company_tickers.json": _Resp(status=503)})
        with pytest.raises(requests.HTTPError):
            _load_cik_map(session, sec._RateLimiter(0))

    # Adversarial: a CIK is interpolated into URL paths, so only ASCII digits pass.
    @pytest.mark.parametrize(
        "bad_cik", ["320193/../../x", "32O193", "３２０", "-320193", "", "0", True]
    )
    def test_non_digit_cik_raises(self, bad_cik):
        payload = {"0": {"cik_str": bad_cik, "ticker": "AAPL", "title": "Apple"}}
        session = _FakeSession({"company_tickers.json": _Resp(json_data=payload)})
        with pytest.raises(ValueError):
            _load_cik_map(session, sec._RateLimiter(0))


# ---------------------------------------------------------------------------
# AC2, AC3: update_sec_data uses the CIK map
# ---------------------------------------------------------------------------


class TestUpdateSecDataCik:
    def test_mapped_cik_wins_over_stored_url(self, tmp_db, config, fake_sec, caplog):
        tmp_db.execute(
            sa.insert(sec_filings).values(
                ticker="XOM",
                form_type="10-K",
                filed_date="2025-02-01",
                accession_no="0000034088-25-000001",
                filing_url="https://www.sec.gov/Archives/edgar/data/34088/000003408825000001/",
                fetched_at="2025-02-02",
            )
        )
        tmp_db.commit()
        session = fake_sec(
            {
                "company_tickers.json": _Resp(json_data=_tickers_json({"XOM": 2115436})),
                "CIK0002115436.json": _Resp(json_data=_subs([])),
            }
        )
        with caplog.at_level(logging.WARNING, logger="data.sec_data"):
            update_sec_data(tmp_db, ["XOM"], config, forms=["4"])

        assert any("CIK0002115436.json" in u for u in session.urls)
        assert not any("CIK0000034088" in u for u in session.urls)
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("XOM" in m and "34088" in m and "2115436" in m for m in warnings)

    def test_no_warning_when_cik_unchanged(self, tmp_db, config, fake_sec, caplog):
        tmp_db.execute(
            sa.insert(sec_filings).values(
                ticker="AAPL",
                form_type="10-K",
                filed_date="2025-02-01",
                accession_no="0000320193-25-000001",
                filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/",
                fetched_at="2025-02-02",
            )
        )
        tmp_db.commit()
        fake_sec(
            {
                "company_tickers.json": _Resp(json_data=_tickers_json({"AAPL": 320193})),
                "CIK0000320193.json": _Resp(json_data=_subs([])),
            }
        )
        with caplog.at_level(logging.WARNING, logger="data.sec_data"):
            update_sec_data(tmp_db, ["AAPL"], config, forms=["4"])
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_ticker_missing_from_map_skipped(self, tmp_db, config, fake_sec, caplog):
        session = fake_sec(
            {
                "company_tickers.json": _Resp(json_data=_tickers_json({"AAPL": 320193})),
                "CIK0000320193.json": _Resp(json_data=_subs([])),
            }
        )
        with caplog.at_level(logging.WARNING, logger="data.sec_data"):
            summary = update_sec_data(tmp_db, ["AAPL", "GEV"], config, forms=["4"])

        assert summary["GEV"] == 0
        assert any("GEV" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
        assert any("CIK0000320193.json" in u for u in session.urls)
        assert not any("browse-edgar" in u for u in session.urls)

    def test_map_failure_stores_nothing(self, tmp_db, config, fake_sec, caplog):
        session = fake_sec({"company_tickers.json": _Resp(status=500)})
        with caplog.at_level(logging.ERROR, logger="data.sec_data"):
            summary = update_sec_data(tmp_db, ["AAPL", "MSFT"], config, forms=["4"])

        assert sum(summary.values()) == 0
        assert session.urls == ["https://www.sec.gov/files/company_tickers.json"]
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        count = tmp_db.execute(sa.select(sa.func.count()).select_from(sec_filings)).scalar()
        assert count == 0

    def test_never_calls_browse_edgar(self, tmp_db, config, fake_sec):
        session = fake_sec(
            {
                "company_tickers.json": _Resp(json_data=_tickers_json({"MSFT": 789019})),
                "CIK0000789019.json": _Resp(json_data=_subs([])),
            }
        )
        update_sec_data(tmp_db, ["MSFT", "KVUE"], config, forms=["4"])
        assert not hasattr(sec, "_ticker_to_cik")
        assert not any("browse-edgar" in u for u in session.urls)

    # Adversarial: a malicious map entry must not reach any URL or the database.
    def test_map_with_path_in_cik_stores_nothing(self, tmp_db, config, fake_sec):
        payload = {"0": {"cik_str": "320193/../../evil", "ticker": "AAPL", "title": "x"}}
        session = fake_sec({"company_tickers.json": _Resp(json_data=payload)})
        update_sec_data(tmp_db, ["AAPL"], config, forms=["4"])
        assert session.urls == ["https://www.sec.gov/files/company_tickers.json"]


# ---------------------------------------------------------------------------
# AC5, AC6: issuer check and full key in update_sec_data
# ---------------------------------------------------------------------------


def _run_aapl(tmp_db, config, fake_sec, filings: dict[str, str]):
    """Run update_sec_data for AAPL with the given {accession: ownership XML} Form 4s."""
    routes = {
        "company_tickers.json": _Resp(json_data=_tickers_json({"AAPL": 320193})),
        "CIK0000320193.json": _Resp(json_data=_subs(list(filings))),
    }
    for acc, xml in filings.items():
        routes[f"/{acc}.txt"] = _Resp(text=_sgml(xml))
    fake_sec(routes)
    return update_sec_data(tmp_db, ["AAPL"], config, forms=["4"])


class TestUpdateSecDataIssuer:
    def test_other_issuer_not_stored(self, tmp_db, config, fake_sec, caplog):
        acc = "0000320193-26-000001"
        with caplog.at_level(logging.INFO, logger="data.sec_data"):
            _run_aapl(tmp_db, config, fake_sec, {acc: _form4(issuer_cik=LENNAR_CIK)})

        filing = tmp_db.execute(
            sa.select(sec_filings.c.ticker, sec_filings.c.content_text).where(
                sec_filings.c.accession_no == acc
            )
        ).fetchone()
        assert filing is not None and filing[0] == "AAPL"
        assert "ownershipDocument" in filing[1]
        assert _rows(tmp_db) == []
        assert any("issuer" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)

    def test_same_issuer_stored(self, tmp_db, config, fake_sec):
        acc = "0000320193-26-000002"
        _run_aapl(tmp_db, config, fake_sec, {acc: _form4(issuer_cik="0000320193")})
        assert _rows(tmp_db) == [("AAPL", acc, 0, "P", 1000.0)]

    def test_missing_issuer_not_stored(self, tmp_db, config, fake_sec):
        _run_aapl(tmp_db, config, fake_sec, {"0000320193-26-000003": _form4(issuer_cik=None)})
        assert _rows(tmp_db) == []

    def test_same_shares_both_stored(self, tmp_db, config, fake_sec):
        acc = "0000320193-26-000004"
        xml = _form4(txns=[("M", 43778, _today()), ("S", 43778, _today())])
        _run_aapl(tmp_db, config, fake_sec, {acc: xml})
        assert _rows(tmp_db) == [
            ("AAPL", acc, 0, "M", 43778.0),
            ("AAPL", acc, 1, "S", 43778.0),
        ]

    # Adversarial: two lines identical in every field, code included (NVDA two S lots).
    def test_identical_lines_both_stored(self, tmp_db, config, fake_sec):
        acc = "0000320193-26-000005"
        xml = _form4(txns=[("S", 300000, _today()), ("S", 300000, _today())])
        _run_aapl(tmp_db, config, fake_sec, {acc: xml})
        assert [r[2] for r in _rows(tmp_db)] == [0, 1]

    # Adversarial: issuer CIK that only shares a prefix with the ticker's CIK is a mismatch.
    def test_prefix_cik_is_mismatch(self, tmp_db, config, fake_sec):
        _run_aapl(tmp_db, config, fake_sec, {"0000320193-26-000006": _form4(issuer_cik="3201930")})
        assert _rows(tmp_db) == []


# ---------------------------------------------------------------------------
# AC8, AC9: rebuild from stored XML
# ---------------------------------------------------------------------------

_REBUILD_MAP = {"AAPL": AAPL_CIK, "BRK-B": "1067983", "GOOG": ALPHABET_CIK, "GOOGL": ALPHABET_CIK}
_UNIVERSE = ["AAPL", "BRK-B", "GOOG", "GOOGL"]


def _store_form4(conn, ticker, acc, xml, feed_cik="1"):
    conn.execute(
        sa.insert(sec_filings).values(
            ticker=ticker,
            form_type="4",
            filed_date=_today(),
            accession_no=acc,
            filing_url=(
                f"https://www.sec.gov/Archives/edgar/data/{feed_cik}/{acc.replace('-', '')}/"
            ),
            content_text=xml,
            fetched_at=_today(),
        )
    )


def _seed_rebuild(conn):
    """Stored Form 4s plus the defective rows and flags the old code produced."""
    _store_form4(conn, "AAPL", "acc-aapl-1", _form4(AAPL_CIK, "ALICE", txns=[("P", 10, _today())]))
    _store_form4(conn, "AAPL", "acc-aapl-2", _form4(AAPL_CIK, "BOB", txns=[("P", 20, _today())]))
    _store_form4(conn, "AAPL", "acc-aapl-3", _form4(AAPL_CIK, "CAROL", txns=[("P", 30, _today())]))
    # Berkshire as reporting owner buying Lennar: filed under BRK-B, issuer is Lennar.
    lennar = _form4(LENNAR_CIK, "BERKSHIRE", txns=[("P", 1, _today())] * 3)
    _store_form4(conn, "BRK-B", "acc-brk-lennar", lennar)
    # Alphabet filing row owned by GOOGL only (accession_no is globally unique).
    _store_form4(
        conn, "GOOGL", "acc-goog-1", _form4(ALPHABET_CIK, "PICHAI", txns=[("S", 5, _today())])
    )
    # Old, wrong data: BRK-B rows from Lennar, a stale BRK-B cluster flag.
    for i, name in enumerate(["X", "Y", "Z"]):
        _insert_purchase(conn, "BRK-B", name, _today(), shares=100 + i)
    conn.execute(
        sa.insert(insider_cluster_flags).values(
            ticker="BRK-B", window_start=_today(), window_end=_today(), insider_count=3
        )
    )
    conn.commit()


def _flags(conn):
    return sorted(
        conn.execute(
            sa.select(insider_cluster_flags.c.ticker, insider_cluster_flags.c.insider_count)
        ).fetchall()
    )


@pytest.fixture
def rebuild_map(monkeypatch):
    calls = []

    def fake_load(session, limiter):
        calls.append(1)
        return dict(_REBUILD_MAP)

    monkeypatch.setattr(sec, "_load_cik_map", fake_load)
    monkeypatch.setattr(sec, "_make_session", lambda config: _FakeSession({}))
    return calls


class TestRebuildInsider:
    def test_rebuild_replaces_rows(self, tmp_db, config, rebuild_map):
        _seed_rebuild(tmp_db)
        result = rebuild_insider_transactions(tmp_db, _UNIVERSE, config)

        assert _rows(tmp_db, "BRK-B") == []
        assert _rows(tmp_db, "AAPL") == [
            ("AAPL", "acc-aapl-1", 0, "P", 10.0),
            ("AAPL", "acc-aapl-2", 0, "P", 20.0),
            ("AAPL", "acc-aapl-3", 0, "P", 30.0),
        ]
        assert result == {"AAPL": 3, "BRK-B": 0, "GOOG": 1, "GOOGL": 1}

    def test_share_classes_both_receive(self, tmp_db, config, rebuild_map):
        _seed_rebuild(tmp_db)
        rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        assert _rows(tmp_db, "GOOG") == [("GOOG", "acc-goog-1", 0, "S", 5.0)]
        assert _rows(tmp_db, "GOOGL") == [("GOOGL", "acc-goog-1", 0, "S", 5.0)]

    def test_stale_flags_removed_and_recomputed(self, tmp_db, config, rebuild_map):
        _seed_rebuild(tmp_db)
        rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        assert _flags(tmp_db) == [("AAPL", 3)]

    def test_logs_counts(self, tmp_db, config, rebuild_map, caplog):
        _seed_rebuild(tmp_db)
        with caplog.at_level(logging.INFO, logger="data.sec_data"):
            rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        msg = next(r.getMessage() for r in caplog.records if "Insider rebuild" in r.getMessage())
        assert "5 Form 4 filings parsed" in msg
        assert "1 with no universe issuer" in msg
        assert "5 rows inserted" in msg
        assert "1 cluster flags" in msg

    def test_failure_leaves_data_unchanged(self, tmp_db, config, rebuild_map, monkeypatch):
        _seed_rebuild(tmp_db)
        before_rows, before_flags = _rows(tmp_db), _flags(tmp_db)

        def boom(conn, ticker, config):
            raise RuntimeError("disk full")

        monkeypatch.setattr(sec, "_write_cluster_flags", boom)
        with pytest.raises(RuntimeError):
            rebuild_insider_transactions(tmp_db, _UNIVERSE, config)

        assert _rows(tmp_db) == before_rows
        assert _flags(tmp_db) == before_flags

    def test_map_failure_leaves_data_unchanged(self, tmp_db, config, monkeypatch):
        _seed_rebuild(tmp_db)
        before = _rows(tmp_db)

        def fail(session, limiter):
            raise requests.ConnectionError("offline")

        monkeypatch.setattr(sec, "_load_cik_map", fail)
        monkeypatch.setattr(sec, "_make_session", lambda config: _FakeSession({}))
        with pytest.raises(requests.ConnectionError):
            rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        assert _rows(tmp_db) == before

    # Adversarial: running the rebuild twice gives the same rows (no duplicates).
    def test_rebuild_is_repeatable(self, tmp_db, config, rebuild_map):
        _seed_rebuild(tmp_db)
        rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        first = _rows(tmp_db), _flags(tmp_db)
        rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        assert (_rows(tmp_db), _flags(tmp_db)) == first

    # Adversarial: malformed stored XML is skipped, not fatal.
    def test_malformed_xml_skipped(self, tmp_db, config, rebuild_map):
        _seed_rebuild(tmp_db)
        _store_form4(tmp_db, "AAPL", "acc-bad", "<ownershipDocument><issuer>")
        tmp_db.commit()
        result = rebuild_insider_transactions(tmp_db, _UNIVERSE, config)
        assert result["AAPL"] == 3

    # Adversarial: a renamed ticker's filings (BK) reach the current ticker (BNY) by issuer CIK,
    # and nothing is written for the old ticker outside the universe.
    def test_renamed_ticker_mapped_by_issuer(self, tmp_db, config, monkeypatch):
        _store_form4(
            tmp_db, "BK", "acc-bk-1", _form4("1390777", "GIBBONS", txns=[("S", 7, _today())])
        )
        tmp_db.commit()
        monkeypatch.setattr(sec, "_load_cik_map", lambda s, lim: {"BNY": "1390777", "BK": "1"})
        monkeypatch.setattr(sec, "_make_session", lambda config: _FakeSession({}))
        rebuild_insider_transactions(tmp_db, ["BNY"], config)
        assert _rows(tmp_db) == [("BNY", "acc-bk-1", 0, "S", 7.0)]

    # Adversarial: a universe ticker missing from the map gets no rows and does not break others.
    def test_unmapped_universe_ticker(self, tmp_db, config, rebuild_map):
        _seed_rebuild(tmp_db)
        result = rebuild_insider_transactions(tmp_db, _UNIVERSE + ["GEV"], config)
        assert result["GEV"] == 0
        assert result["AAPL"] == 3


class TestRebuildPredecessorCik:
    """XOM's stored Form 4s name its predecessor CIK, not the CIK it maps to today."""

    def _rebuild(self, conn, config, monkeypatch, tickers):
        cik_map = {"XOM": "2115436", "BNY": "1390777"}
        monkeypatch.setattr(sec, "_load_cik_map", lambda s, lim: dict(cik_map))
        monkeypatch.setattr(sec, "_make_session", lambda config: _FakeSession({}))
        config["sec"]["predecessor_ciks"] = {"XOM": ["34088"]}
        conn.commit()
        return rebuild_insider_transactions(conn, tickers, config)

    def test_configured_predecessor_kept(self, tmp_db, config, monkeypatch):
        xml = _form4("34088", "WOODS", txns=[("S", 9, _today())], symbol="XOM")
        _store_form4(tmp_db, "XOM", "acc-xom-1", xml, feed_cik="34088")
        self._rebuild(tmp_db, config, monkeypatch, ["XOM"])
        assert _rows(tmp_db) == [("XOM", "acc-xom-1", 0, "S", 9.0)]

    # Adversarial: an unconfigured CIK is excluded even when its symbol is a universe ticker
    # (a BlackRock municipal fund trades as "BNY").
    def test_unconfigured_cik_with_matching_symbol_excluded(self, tmp_db, config, monkeypatch):
        xml = _form4("1137390", "HUBBARD", txns=[("P", 3, _today())], symbol="BNY")
        _store_form4(tmp_db, "BNY", "acc-myn-1", xml, feed_cik="1137390")
        self._rebuild(tmp_db, config, monkeypatch, ["BNY"])
        assert _rows(tmp_db) == []

    # Adversarial: a configured predecessor for a ticker outside the universe writes nothing.
    def test_predecessor_outside_universe_excluded(self, tmp_db, config, monkeypatch):
        xml = _form4("34088", "WOODS", txns=[("S", 9, _today())], symbol="XOM")
        _store_form4(tmp_db, "XOM", "acc-xom-1", xml, feed_cik="34088")
        self._rebuild(tmp_db, config, monkeypatch, ["BNY"])
        assert _rows(tmp_db) == []

    # Adversarial: the shipped config carries XOM's predecessor CIK.
    def test_shipped_config_lists_xom_predecessor(self, config):
        assert config["sec"]["predecessor_ciks"] == {"XOM": ["34088"]}
