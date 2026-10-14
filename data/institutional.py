"""13-F institutional holdings ingestion from SEC EDGAR."""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET  # nosec B405
from collections import Counter
from datetime import datetime

import requests
import sqlalchemy as sa

from .db import (
    insert_or_ignore,
    insert_or_replace,
    institutional_holdings,
    institutional_summary,
    sp500_universe,
)

logger = logging.getLogger(__name__)

_EDGAR_ARCH = "https://www.sec.gov"
_SUBMISSIONS = "https://data.sec.gov/submissions"
_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_OPENFIGI_BATCH = 100
_US_EXCHANGES = ("US", "UN", "UA", "UQ", "UR", "UT")
_OPENFIGI_SLEEP = 0.5  # 25 req/10 s free-tier limit → stay at 2/s
_MIN_INTERVAL = 1.0 / 8  # 8 req/s (SEC)
_XML_NAME = re.compile(r"[\w.-]+\.xml", re.IGNORECASE)  # plain filename, no path


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


def _get_recent_13f_accessions(
    session: requests.Session, cik: str, limiter: _RateLimiter, max_filings: int = 6
) -> list[tuple[str, str, str]]:
    """Return (accession, filed_date, period) for the latest original 13F-HR per period.

    Amendments (13F-HR/A) are skipped: originals give one consistent filing per quarter.
    """
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
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    periods = recent.get("reportDate", [])

    results = []
    seen: set[str] = set()
    rows = sorted(zip(forms, dates, accs, periods, strict=False), key=lambda r: r[1], reverse=True)
    # Newest first, so the first filing seen for a period wins
    for ft, fd, acc, period in rows:
        if ft != "13F-HR" or not period or period in seen:
            continue
        seen.add(period)
        results.append((acc, fd, period))
        if len(results) >= max_filings:
            break
    return results


def _infotable_filenames(session: requests.Session, base: str, limiter: _RateLimiter) -> list[str]:
    """Infotable candidates from the filing's index.json; legacy names if none are listed."""
    names: list[str] = []
    limiter.wait()
    try:
        resp = session.get(f"{base}/index.json", timeout=30)
        resp.raise_for_status()
        items = resp.json()["directory"]["item"]
        names = [
            i["name"]
            for i in items
            if _XML_NAME.fullmatch(i["name"]) and i["name"] != "primary_doc.xml"
        ]
    except Exception as exc:
        logger.debug("index.json fetch failed (%s): %s", base, exc)
    return names or ["infotable.xml", "form13fInfoTable.xml"]


def _fetch_13f_infotable(
    session: requests.Session,
    cik: str,
    accession: str,
    limiter: _RateLimiter,
    conn: sa.engine.Connection,
    cusip_cache: dict[str, str | None] | None = None,
) -> list[dict]:
    """Download, parse, and resolve a 13-F infotable to S&P 500 tickers (others dropped)."""
    acc_clean = accession.replace("-", "")
    cik_clean = cik.lstrip("0")
    base = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik_clean}/{acc_clean}"

    raw: list[dict] = []
    for filename in _infotable_filenames(session, base, limiter):
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

    resolved = _resolve_tickers(raw, conn, cusip_cache)
    logger.debug("13F %s: %d/%d rows resolved to tickers", accession, len(resolved), len(raw))
    return resolved


def _resolve_tickers(
    raw: list[dict], conn: sa.engine.Connection, cusip_cache: dict[str, str | None] | None
) -> list[dict]:
    """Fill tickers by issuer name, then CUSIP; keep S&P 500 universe holdings only."""
    name_map = _build_name_ticker_map(conn)
    universe = set(conn.execute(sa.select(sp500_universe.c.ticker)).scalars())
    needs = [h for h in raw if not h["ticker"]]
    if needs:
        # Primary: match issuer name against our stored S&P 500 universe
        for h in needs:
            h["ticker"] = name_map.get(_norm_name(h["name"]))

        # Fallback: CUSIP lookup via OpenFIGI (only when API key is configured)
        still_unresolved = [h for h in needs if not h["ticker"] and h.get("cusip")]
        if still_unresolved:
            cusip_map = _cusip_to_tickers_openfigi(
                sorted({h["cusip"] for h in still_unresolved}), cusip_cache
            )
            for h in still_unresolved:
                h["ticker"] = cusip_map.get(h["cusip"])

    for h in raw:
        if h.get("ticker"):
            # Universe spells share classes BRK-B; OpenFIGI says BRK/B, filings may say BRK.B
            h["ticker"] = re.sub(r"[./]", "-", h["ticker"])
    resolved = [h for h in raw if h.get("ticker") in universe]
    return resolved


def _normalise_value_units(rows: list[dict]) -> bool:
    """Rescale a filing still reported in $ thousands; True if it was rescaled.

    No S&P 500 stock trades near $1, so a median implied price below $1 means thousands.
    """
    prices = sorted(
        r["market_value"] / r["shares"]
        for r in rows
        if (r.get("shares") or 0) > 0 and (r.get("market_value") or 0) > 0
    )
    if not prices or prices[len(prices) // 2] >= 1.0:
        return False
    for r in rows:
        if r.get("market_value") is not None:
            r["market_value"] *= 1000
    return True


def _aggregate_by_ticker(rows: list[dict]) -> list[dict]:
    """Sum shares and value per ticker; a filing can list one issuer on several rows."""
    totals: dict[str, dict] = {}
    for row in rows:
        agg = totals.setdefault(
            row["ticker"], {"ticker": row["ticker"], "shares": None, "market_value": None}
        )
        for key in ("shares", "market_value"):
            if row.get(key) is not None:
                agg[key] = (agg[key] or 0.0) + row[key]
    return list(totals.values())


def _parse_infotable(xml_text: str) -> list[dict]:
    """Parse 13-F infotable XML.

    Real EDGAR filings carry CUSIP numbers but no ticker; test fixtures may
    include an <issuerTicker> element.  Rows with neither are skipped, as are
    option rows (<putCall>) and principal amounts (PRN, i.e. bonds).
    """
    holdings = []
    try:
        root = ET.fromstring(xml_text)  # nosec B314
    except ET.ParseError:
        return holdings

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    for entry in root.findall(f"{ns}infoTable"):
        ticker_el = entry.find(f"{ns}issuerTicker")
        if ticker_el is None:
            ticker_el = entry.find(f"{ns}tickerSymbol")
        cusip_el = entry.find(f"{ns}cusip")
        name_el = entry.find(f"{ns}nameOfIssuer")
        shares_el = entry.find(f"{ns}shrsOrPrnAmt/{ns}sshPrnamt")
        value_el = entry.find(f"{ns}value")
        if not _is_common_stock(entry, ns):
            continue

        ticker = (ticker_el.text or "").strip().upper() if ticker_el is not None else ""
        cusip = (cusip_el.text or "").strip() if cusip_el is not None else ""
        name = (name_el.text or "").strip() if name_el is not None else ""

        if not ticker and not cusip:
            continue

        try:
            shares = float((shares_el.text or "0").replace(",", ""))
        except (ValueError, AttributeError):
            shares = None
        try:
            # Whole dollars since 2023-01-03 (previously $ thousands)
            value = float((value_el.text or "0").replace(",", ""))
        except (ValueError, AttributeError):
            value = None

        holdings.append(
            {
                "ticker": ticker or None,
                "cusip": cusip or None,
                "name": name,
                "shares": shares,
                "market_value": value,
            }
        )

    return holdings


def _is_common_stock(entry: ET.Element, ns: str) -> bool:
    """False for option rows (<putCall>) and principal amounts (PRN)."""
    put_call = entry.findtext(f"{ns}putCall") or ""
    amount_type = entry.findtext(f"{ns}shrsOrPrnAmt/{ns}sshPrnamtType") or ""
    return not put_call.strip() and amount_type.strip().upper() != "PRN"


_CORP_NOISE = re.compile(
    r"\b(INC|CORP|CORPORATION|LTD|LIMITED|LLC|PLC|CO|COMPANY|GROUP|HOLDINGS|"
    r"TECHNOLOGIES|TECHNOLOGY|ENTERPRISES|INTERNATIONAL|FINANCIAL|SERVICES|"
    r"SYSTEMS|SOLUTIONS|COMMUNICATIONS|CL [AB]|CLASS [AB]|COM)\b",
    re.IGNORECASE,
)


def _prior_quarter_report_date(date_str: str | None) -> str | None:
    """Return the report date for the prior quarter (subtract 3 months, preserve day string)."""
    if not date_str:
        return None
    try:
        parts = str(date_str).split("-")
        if len(parts) != 3:
            return None
        year, month, day = int(parts[0]), int(parts[1]), parts[2]
        month -= 3
        if month <= 0:
            month += 12
            year -= 1
        return f"{year}-{month:02d}-{day}"
    except (ValueError, AttributeError):
        return None


def _norm_name(name: str) -> str:
    name = _CORP_NOISE.sub("", name.upper())
    return re.sub(r"[^\w]+", " ", name).strip()


def _build_name_ticker_map(conn: sa.engine.Connection) -> dict[str, str]:
    """Normalised company name → ticker for the stored S&P 500 universe.

    Names shared by several tickers (share classes: FOX/FOXA, NWS/NWSA) are left out,
    so those holdings fall through to the CUSIP lookup, which can tell the classes apart.
    """
    rows = conn.execute(
        sa.select(sp500_universe.c.ticker, sp500_universe.c.company_name)
    ).fetchall()
    by_name: dict[str, set[str]] = {}
    for ticker, name in rows:
        if name:
            by_name.setdefault(_norm_name(name), set()).add(ticker)
    return {name: next(iter(t)) for name, t in by_name.items() if len(t) == 1}


def _cusip_to_tickers_openfigi(
    cusips: list[str], cache: dict[str, str | None] | None = None
) -> dict[str, str]:
    """CUSIP/CINS → US common-stock ticker via OpenFIGI.  Requires OPENFIGI_API_KEY.

    `cache` (shared across one run) records answers, including unresolved ones, so each
    identifier is looked up once; identifiers from failed batches are left out to retry.
    """
    api_key = os.getenv("OPENFIGI_API_KEY", "").strip()
    if not api_key:
        return {}
    cache = {} if cache is None else cache

    session = requests.Session()
    session.headers.update({"Content-Type": "application/json", "X-OPENFIGI-APIKEY": api_key})
    todo = [c for c in cusips if c not in cache]
    for i in range(0, len(todo), _OPENFIGI_BATCH):
        batch = todo[i : i + _OPENFIGI_BATCH]
        try:
            resp = session.post(
                _OPENFIGI_URL,
                # CINS (non-US issuers) start with a letter and need their own id type
                json=[
                    {"idType": "ID_CINS" if c[:1].isalpha() else "ID_CUSIP", "idValue": c}
                    for c in batch
                ],
                timeout=30,
            )
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                raise ValueError(f"unexpected response type {type(items).__name__}")
        except Exception as exc:
            logger.warning("OpenFIGI batch %d failed: %s", i // _OPENFIGI_BATCH, exc)
            continue
        finally:
            time.sleep(_OPENFIGI_SLEEP)
        for j, cusip in enumerate(batch):
            item = items[j] if j < len(items) and isinstance(items[j], dict) else {}
            cache[cusip] = _us_common_ticker(item.get("data", []))

    result = {c: cache[c] for c in cusips if cache.get(c)}
    logger.debug("OpenFIGI resolved %d/%d CUSIPs", len(result), len(cusips))
    return result


def _us_common_ticker(hits: list[dict]) -> str | None:
    """First US-listed common stock ticker; foreign listings can collide with US tickers."""
    for hit in hits:
        if (
            hit.get("securityType") == "Common Stock"
            and hit.get("exchCode", "") in _US_EXCHANGES
            and hit.get("ticker")
        ):
            return hit["ticker"]
    return None


def _summarise_holdings(conn: sa.engine.Connection) -> None:
    """Rebuild institutional_summary per (ticker, period), comparing each fund with itself.

    Funds file on different days, so each fund is compared with its own previous
    filing, and funds yet to file for a period count with their latest earlier one.
    """
    rows = conn.execute(
        sa.select(
            institutional_holdings.c.fund_name,
            institutional_holdings.c.ticker,
            institutional_holdings.c.report_date,
            institutional_holdings.c.shares_held,
        )
    ).fetchall()

    conn.execute(sa.delete(institutional_summary))
    for values in summarise_rows(rows):
        conn.execute(insert_or_replace(conn, institutional_summary).values(**values))

    conn.commit()
    logger.debug("Institutional summary rebuilt")


def summarise_rows(rows) -> list[dict]:
    """Summary rows for (fund, ticker, period, shares) holdings; rules in _summarise_holdings."""
    filings: dict[tuple[str, str], dict[str, float]] = {}
    for fund, ticker, period, shares in rows:
        filings.setdefault((fund, period), {})[ticker] = shares or 0.0
    fund_periods: dict[str, list[str]] = {}
    for fund, period in sorted(filings):
        fund_periods.setdefault(fund, []).append(period)

    return [
        values
        for period in sorted({p for _, p in filings})
        for values in _summarise_period(filings, fund_periods, period)
    ]


def _summarise_period(
    filings: dict[tuple[str, str], dict[str, float]],
    fund_periods: dict[str, list[str]],
    period: str,
) -> list[dict]:
    """Summary rows for one period; see _summarise_holdings for the rules."""
    funds_holding: Counter[str] = Counter()
    new_positions: Counter[str] = Counter()
    net_change: dict[str, float] = {}
    compared = False

    for fund, periods in fund_periods.items():
        as_of = [p for p in periods if p <= period]
        if not as_of:
            continue
        current = filings[(fund, as_of[-1])]
        funds_holding.update(current.keys())
        if as_of[-1] != period or len(as_of) < 2:
            continue  # not filed for this period yet, or no baseline to compare with
        compared = True
        prior = filings[(fund, as_of[-2])]
        for ticker in current.keys() | prior.keys():
            change = current.get(ticker, 0.0) - prior.get(ticker, 0.0)
            net_change[ticker] = net_change.get(ticker, 0.0) + change
            if ticker in current and ticker not in prior:
                new_positions[ticker] += 1

    return [
        {
            "ticker": ticker,
            "report_date": period,
            "funds_holding": funds_holding[ticker],
            "net_share_change": net_change.get(ticker, 0.0) if compared else None,
            "new_positions": new_positions[ticker],
        }
        for ticker in sorted(funds_holding.keys() | net_change.keys())
    ]


def _purge_legacy_rows(conn: sa.engine.Connection) -> int:
    """Delete rows stored before filed_date existed (filing date as period, scaled values)."""
    result = conn.execute(
        sa.delete(institutional_holdings).where(institutional_holdings.c.filed_date.is_(None))
    )
    conn.commit()
    return result.rowcount or 0


def _store_holdings(
    conn: sa.engine.Connection,
    fund_name: str,
    holdings: list[dict],
    report_date: str,
    filed_date: str,
) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    for h in holdings:
        try:
            conn.execute(
                insert_or_ignore(conn, institutional_holdings).values(
                    fund_name=fund_name,
                    ticker=h["ticker"],
                    shares_held=h["shares"],
                    market_value=h["market_value"],
                    report_date=report_date,
                    filed_date=filed_date,
                    fetched_at=now,
                )
            )
        except Exception as exc:
            logger.debug("Institutional insert error: %s", exc)


def _load_filing(
    conn: sa.engine.Connection,
    session: requests.Session,
    limiter: _RateLimiter,
    name: str,
    cik: str,
    accession: str,
    filed_date: str,
    report_date: str,
    cusip_cache: dict[str, str | None],
    refetch: bool = False,
) -> int:
    """Fetch and store one filing; returns rows stored.

    A period already loaded is skipped, or with refetch replaced once the new fetch succeeds.
    """
    this_filing = (institutional_holdings.c.fund_name == name) & (
        institutional_holdings.c.report_date == report_date
    )
    existing = conn.execute(
        sa.select(sa.func.count()).select_from(institutional_holdings).where(this_filing)
    ).scalar()
    if existing > 0 and not refetch:
        logger.debug("13-F for %s %s already loaded (%d rows)", name, report_date, existing)
        return 0

    holdings = _aggregate_by_ticker(
        _fetch_13f_infotable(session, cik, accession, limiter, conn, cusip_cache=cusip_cache)
    )
    if not holdings:
        logger.warning("No holdings parsed for %s %s", name, accession)
        return 0
    if _normalise_value_units(holdings):
        logger.info("13-F %s (%s) reported in $ thousands; rescaled", name, report_date)

    if existing:
        conn.execute(sa.delete(institutional_holdings).where(this_filing))
        logger.info("13-F %s (%s): replaced %d stored rows", name, report_date, existing)
    _store_holdings(conn, name, holdings, report_date, filed_date)
    conn.commit()
    logger.info("13-F %s (%s): %d holdings stored", name, report_date, len(holdings))
    return len(holdings)


def _load_fund(
    conn: sa.engine.Connection,
    session: requests.Session,
    limiter: _RateLimiter,
    fund: dict,
    cusip_cache: dict[str, str | None],
    refetch: bool,
) -> int:
    """Load a fund's recent 13F filings; returns holdings stored."""
    name, cik = fund["name"], fund["cik"]
    filings = _get_recent_13f_accessions(session, cik, limiter)
    if not filings:
        logger.warning("No 13-F found for %s (CIK %s)", name, cik)
        return 0
    return sum(
        _load_filing(
            conn, session, limiter, name, cik, accession, filed, period, cusip_cache, refetch
        )
        for accession, filed, period in filings
    )


def update_institutional(
    conn: sa.engine.Connection,
    config: dict,
    refetch: bool = False,
) -> dict[str, int]:
    """Fetch 13-F filings for tracked funds. Returns {fund_name: holdings_stored}."""
    funds = config["institutional"]["tracked_funds"]
    session = _make_session()
    limiter = _RateLimiter(_MIN_INTERVAL)
    summary: dict[str, int] = {}

    cusip_cache: dict[str, str | None] = {}

    purged = _purge_legacy_rows(conn)
    if purged:
        logger.warning("Removed %d legacy 13-F rows; they will be re-fetched", purged)

    logger.info("Fetching 13-F filings for %d tracked funds", len(funds))

    for fund in funds:
        summary[fund["name"]] = _load_fund(conn, session, limiter, fund, cusip_cache, refetch)

    _summarise_holdings(conn)

    flag_count = config["institutional"]["new_position_flag_count"]
    flagged = conn.execute(
        sa.select(sa.func.count())
        .select_from(institutional_summary)
        .where(institutional_summary.c.new_positions >= flag_count)
    ).scalar()
    if flagged:
        logger.info(
            "Institutional flags: %d tickers with %d+ funds opening new positions",
            flagged,
            flag_count,
        )

    return summary
