"""13-F institutional holdings ingestion from SEC EDGAR."""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import sqlalchemy as sa

from .db import insert_or_ignore, insert_or_replace, institutional_holdings, institutional_summary, sp500_universe

logger = logging.getLogger(__name__)

_EDGAR_ARCH      = "https://www.sec.gov"
_SUBMISSIONS     = "https://data.sec.gov/submissions"
_OPENFIGI_URL    = "https://api.openfigi.com/v3/mapping"
_OPENFIGI_BATCH  = 100
_OPENFIGI_SLEEP  = 0.5   # 25 req/10 s free-tier limit → stay at 2/s
_MIN_INTERVAL    = 1.0 / 8   # 8 req/s (SEC)


class _RateLimiter:
    def __init__(self, interval: float):
        self._interval = interval
        self._last = 0.0

    def wait(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last = time.monotonic()


def _make_session() -> requests.Session:
    s = requests.Session()
    agent = os.getenv("SEC_USER_AGENT", "MeridianCapital/1.0")
    email = os.getenv("SEC_USER_EMAIL", "unknown@example.com")
    s.headers.update({"User-Agent": f"{agent} {email}"})
    return s


def _get_recent_13f_accessions(session: requests.Session, cik: str,
                                limiter: _RateLimiter,
                                max_filings: int = 6) -> list[tuple[str, str]]:
    """Return up to max_filings (accession_number, filed_date) for recent 13-F-HRs."""
    limiter.wait()
    padded = cik.lstrip("0").zfill(10)
    url = f"{_SUBMISSIONS}/CIK{padded}.json"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        subs = resp.json()
    except Exception as exc:
        logger.debug("Submissions fetch failed CIK=%s: %s", cik, exc)
        return []

    recent = subs.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    dates   = recent.get("filingDate", [])
    accs    = recent.get("accessionNumber", [])

    results = []
    for ft, fd, acc in zip(forms, dates, accs, strict=False):
        if ft in ("13F-HR", "13F-HR/A"):
            results.append((acc, fd))
            if len(results) >= max_filings:
                break
    return results


def _fetch_13f_infotable(session: requests.Session, cik: str, accession: str,
                          limiter: _RateLimiter,
                          conn: sa.engine.Connection) -> list[dict]:
    """Download, parse, and ticker-resolve a 13-F infotable XML."""
    acc_clean = accession.replace("-", "")
    cik_clean = cik.lstrip("0")
    base = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik_clean}/{acc_clean}"

    raw: list[dict] = []
    for filename in ("infotable.xml", "form13fInfoTable.xml"):
        limiter.wait()
        try:
            resp = session.get(f"{base}/{filename}", timeout=60)
            if resp.ok and resp.content:
                raw = _parse_infotable(resp.text)
                if raw:
                    logger.debug("13F infotable fetched via %s (%d rows)", filename, len(raw))
                    break
        except Exception as exc:
            logger.debug("infotable fetch failed (%s): %s", filename, exc)

    if not raw:
        logger.debug("No infotable found for %s", accession)
        return []

    needs = [h for h in raw if not h["ticker"]]
    if needs:
        # Primary: match issuer name against our stored S&P 500 universe
        name_map = _build_name_ticker_map(conn)
        for h in needs:
            h["ticker"] = name_map.get(_norm_name(h["name"]))

        # Fallback: CUSIP lookup via OpenFIGI (only when API key is configured)
        still_unresolved = [h for h in needs if not h["ticker"] and h.get("cusip")]
        if still_unresolved:
            cusip_map = _cusip_to_tickers_openfigi(
                list({h["cusip"] for h in still_unresolved})
            )
            for h in still_unresolved:
                h["ticker"] = cusip_map.get(h["cusip"])

    resolved = [h for h in raw if h.get("ticker")]
    logger.debug("13F %s: %d/%d rows resolved to tickers", accession, len(resolved), len(raw))
    return resolved


def _parse_infotable(xml_text: str) -> list[dict]:
    """Parse 13-F infotable XML.

    Real EDGAR filings carry CUSIP numbers but no ticker; test fixtures may
    include an <issuerTicker> element.  Rows with neither are skipped.
    """
    holdings = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return holdings

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    for entry in root.findall(f"{ns}infoTable"):
        ticker_el = entry.find(f"{ns}issuerTicker")
        if ticker_el is None:
            ticker_el = entry.find(f"{ns}tickerSymbol")
        cusip_el  = entry.find(f"{ns}cusip")
        name_el   = entry.find(f"{ns}nameOfIssuer")
        shares_el = entry.find(f"{ns}shrsOrPrnAmt/{ns}sshPrnamt")
        value_el  = entry.find(f"{ns}value")

        ticker = (ticker_el.text or "").strip().upper() if ticker_el is not None else ""
        cusip  = (cusip_el.text or "").strip()          if cusip_el  is not None else ""
        name   = (name_el.text  or "").strip()          if name_el   is not None else ""

        if not ticker and not cusip:
            continue

        try:
            shares = float((shares_el.text or "0").replace(",", ""))
        except (ValueError, AttributeError):
            shares = None
        try:
            value = float((value_el.text or "0").replace(",", "")) * 1000
        except (ValueError, AttributeError):
            value = None

        holdings.append({
            "ticker":       ticker or None,
            "cusip":        cusip  or None,
            "name":         name,
            "shares":       shares,
            "market_value": value,
        })

    return holdings


_CORP_NOISE = re.compile(
    r"\b(INC|CORP|CORPORATION|LTD|LIMITED|LLC|PLC|CO|COMPANY|GROUP|HOLDINGS|"
    r"TECHNOLOGIES|TECHNOLOGY|ENTERPRISES|INTERNATIONAL|FINANCIAL|SERVICES|"
    r"SYSTEMS|SOLUTIONS|COMMUNICATIONS|CL [AB]|CLASS [AB]|COM)\b",
    re.IGNORECASE,
)


def _norm_name(name: str) -> str:
    name = _CORP_NOISE.sub("", name.upper())
    return re.sub(r"[^\w]+", " ", name).strip()


def _build_name_ticker_map(conn: sa.engine.Connection) -> dict[str, str]:
    """Normalised company name → ticker for the stored S&P 500 universe."""
    rows = conn.execute(
        sa.select(sp500_universe.c.ticker, sp500_universe.c.company_name)
    ).fetchall()
    return {_norm_name(name): ticker for ticker, name in rows if name}


def _cusip_to_tickers_openfigi(cusips: list[str]) -> dict[str, str]:
    """CUSIP → ticker via OpenFIGI.  Requires OPENFIGI_API_KEY (free registration)."""
    api_key = os.getenv("OPENFIGI_API_KEY", "").strip()
    if not api_key:
        return {}

    result: dict[str, str] = {}
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json",
                             "X-OPENFIGI-APIKEY": api_key})

    for i in range(0, len(cusips), _OPENFIGI_BATCH):
        batch = cusips[i: i + _OPENFIGI_BATCH]
        try:
            resp = session.post(
                _OPENFIGI_URL,
                json=[{"idType": "ID_CUSIP", "idValue": c} for c in batch],
                timeout=30,
            )
            resp.raise_for_status()
            for cusip, item in zip(batch, resp.json()):
                hits = item.get("data", [])
                for hit in hits:
                    if (hit.get("securityType") in ("Common Stock", "ETP")
                            and hit.get("exchCode", "") in ("US", "UN", "UA", "UQ", "UR", "UT")
                            and hit.get("ticker")):
                        result[cusip] = hit["ticker"]
                        break
                if cusip not in result and hits and hits[0].get("ticker"):
                    result[cusip] = hits[0]["ticker"]
        except Exception as exc:
            logger.debug("OpenFIGI batch %d failed: %s", i // _OPENFIGI_BATCH, exc)
        time.sleep(_OPENFIGI_SLEEP)

    logger.debug("OpenFIGI resolved %d/%d CUSIPs", len(result), len(cusips))
    return result



def _summarise_holdings(conn: sa.engine.Connection) -> None:
    """Aggregate per-ticker metrics from raw institutional_holdings rows."""
    conn.execute(sa.delete(institutional_summary))

    rows = conn.execute(
        sa.select(
            institutional_holdings.c.ticker,
            institutional_holdings.c.report_date,
            sa.func.count(sa.distinct(institutional_holdings.c.fund_name)).label("funds_holding"),
            sa.func.sum(institutional_holdings.c.shares_held).label("total_shares"),
        )
        .group_by(institutional_holdings.c.ticker, institutional_holdings.c.report_date)
    ).fetchall()

    for ticker, report_date, funds, total_shares in rows:
        # Find the most recent prior quarter actually present in the DB —
        # avoids brittle exact-date arithmetic since filing dates shift slightly
        # each quarter.
        prior_date = conn.execute(
            sa.select(sa.func.max(institutional_holdings.c.report_date))
            .where(
                (institutional_holdings.c.ticker == ticker)
                & (institutional_holdings.c.report_date < report_date)
            )
        ).scalar()

        net_change = None
        new_positions = 0
        if prior_date:
            prior = conn.execute(
                sa.select(sa.func.sum(institutional_holdings.c.shares_held))
                .where(
                    (institutional_holdings.c.ticker == ticker)
                    & (institutional_holdings.c.report_date == prior_date)
                )
            ).scalar()
            if prior is not None:
                net_change = (total_shares or 0) - (prior or 0)

            current_funds = set(conn.execute(
                sa.select(institutional_holdings.c.fund_name)
                .where(
                    (institutional_holdings.c.ticker == ticker)
                    & (institutional_holdings.c.report_date == report_date)
                )
            ).scalars().all())
            prior_funds = set(conn.execute(
                sa.select(institutional_holdings.c.fund_name)
                .where(
                    (institutional_holdings.c.ticker == ticker)
                    & (institutional_holdings.c.report_date == prior_date)
                )
            ).scalars().all())
            new_positions = len(current_funds - prior_funds)

        conn.execute(
            insert_or_replace(conn, institutional_summary).values(
                ticker=ticker,
                report_date=report_date,
                funds_holding=funds,
                net_share_change=net_change,
                new_positions=new_positions,
            )
        )

    conn.commit()
    logger.debug("Institutional summary rebuilt")


def update_institutional(
    conn: sa.engine.Connection,
    config: dict,
) -> dict[str, int]:
    """Fetch 13-F filings for tracked funds. Returns {fund_name: holdings_stored}."""
    funds   = config["institutional"]["tracked_funds"]
    session = _make_session()
    limiter = _RateLimiter(_MIN_INTERVAL)
    summary: dict[str, int] = {}

    logger.info("Fetching 13-F filings for %d tracked funds", len(funds))

    for fund in funds:
        name = fund["name"]
        cik  = fund["cik"]

        filings = _get_recent_13f_accessions(session, cik, limiter)
        if not filings:
            logger.warning("No 13-F found for %s (CIK %s)", name, cik)
            summary[name] = 0
            continue

        total_stored = 0
        for accession, filed_date in filings:
            report_date = filed_date[:10]

            existing = conn.execute(
                sa.select(sa.func.count())
                .select_from(institutional_holdings)
                .where(
                    (institutional_holdings.c.fund_name == name)
                    & (institutional_holdings.c.report_date == report_date)
                )
            ).scalar()
            if existing > 0:
                logger.debug("13-F for %s %s already loaded (%d rows)", name, report_date, existing)
                continue

            holdings = _fetch_13f_infotable(session, cik, accession, limiter, conn)
            if not holdings:
                logger.warning("No holdings parsed for %s %s", name, accession)
                continue

            now = datetime.utcnow().isoformat(timespec="seconds")
            for h in holdings:
                try:
                    conn.execute(
                        insert_or_ignore(conn, institutional_holdings).values(
                            fund_name=name,
                            ticker=h["ticker"],
                            shares_held=h["shares"],
                            market_value=h["market_value"],
                            report_date=report_date,
                            fetched_at=now,
                        )
                    )
                except Exception as exc:
                    logger.debug("Institutional insert error: %s", exc)

            conn.commit()
            total_stored += len(holdings)
            logger.info("13-F %s (%s): %d holdings stored", name, report_date, len(holdings))

        summary[name] = total_stored

    _summarise_holdings(conn)

    flag_count = config["institutional"]["new_position_flag_count"]
    flagged = conn.execute(
        sa.select(sa.func.count())
        .select_from(institutional_summary)
        .where(institutional_summary.c.new_positions >= flag_count)
    ).scalar()
    if flagged:
        logger.info("Institutional flags: %d tickers with %d+ funds opening new positions",
                    flagged, flag_count)

    return summary
