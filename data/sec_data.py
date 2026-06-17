"""SEC EDGAR EFTS data ingestion — 10-K, 10-Q, 8-K, Form 4 insider transactions."""

import logging
import os
import re
import time
from datetime import datetime, timedelta

import requests
import sqlalchemy as sa

from .db import insert_or_ignore, insert_or_replace, insider_cluster_flags, insider_transactions, sec_filings

logger = logging.getLogger(__name__)

_EFTS_SEARCH  = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_DATA   = "https://data.sec.gov"       # JSON APIs (submissions, company facts)
_EDGAR_ARCH   = "https://www.sec.gov"        # actual filing documents / archives
_SUBMISSIONS  = "https://data.sec.gov/submissions"

# SEC fair-use: 10 req/s max; we stay at 8
_MIN_INTERVAL = 1.0 / 8


class _RateLimiter:
    def __init__(self, min_interval: float):
        self._min = min_interval
        self._last = 0.0

    def wait(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self._min:
            time.sleep(self._min - elapsed)
        self._last = time.monotonic()


def _make_session(config: dict) -> requests.Session:
    s = requests.Session()
    agent = os.getenv("SEC_USER_AGENT", "MeridianCapital/1.0")
    email = os.getenv("SEC_USER_EMAIL", "unknown@example.com")
    s.headers.update({"User-Agent": f"{agent} {email}"})
    return s


def _ticker_to_cik(session: requests.Session, ticker: str,
                   limiter: _RateLimiter, cache: dict) -> str | None:
    if ticker in cache:
        return cache[ticker]
    limiter.wait()
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker={ticker}&type=&dateb=&owner=include&count=1&output=atom"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        match = re.search(r"CIK=(\d+)", resp.text)
        if match:
            cik = match.group(1).lstrip("0")
            cache[ticker] = cik
            return cik
    except Exception as exc:
        logger.debug("CIK lookup failed for %s: %s", ticker, exc)
    return None


def _get_filings_efts(session: requests.Session, ticker: str, form_type: str,
                      limiter: _RateLimiter, count: int = 5) -> list[dict]:
    """Search EDGAR full-text search for filings by ticker + form type."""
    limiter.wait()
    params = {
        "q": f'"{ticker}"',
        "dateRange": "custom",
        "startdt": (datetime.utcnow() - timedelta(days=365 * 3)).strftime("%Y-%m-%d"),
        "enddt": datetime.utcnow().strftime("%Y-%m-%d"),
        "forms": form_type,
        "_source": "file-index",
        "hits.hits._source": "period_of_report,file_date,period_of_report,accession_no,display_date_filed,file_num",
    }
    try:
        resp = session.get(_EFTS_SEARCH, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("EFTS search failed %s %s: %s", ticker, form_type, exc)
        return []

    hits = data.get("hits", {}).get("hits", [])
    results = []
    for hit in hits[:count]:
        src = hit.get("_source", {})
        results.append({
            "accession_no": src.get("accession_no", ""),
            "filed_date": src.get("file_date", ""),
            "form_type": form_type,
        })
    return results


def _get_submissions(session: requests.Session, cik: str,
                     limiter: _RateLimiter) -> dict:
    """Fetch full submissions JSON for a CIK."""
    limiter.wait()
    padded = cik.zfill(10)
    url = f"{_SUBMISSIONS}/CIK{padded}.json"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("Submissions fetch failed CIK=%s: %s", cik, exc)
        return {}


def _extract_form4_xml(text: str) -> str:
    """Extract <ownershipDocument> XML from an EDGAR SGML submission text file."""
    start = text.find("<ownershipDocument")
    if start == -1:
        return text  # already clean XML or unrecognised format
    end = text.find("</ownershipDocument>", start)
    if end == -1:
        return text
    return text[start: end + len("</ownershipDocument>")]


def _fetch_filing_text(session: requests.Session, cik: str, accession_no: str,
                       primary_doc: str, limiter: _RateLimiter) -> str:
    """Fetch and strip the HTML text of a filing's primary document.

    primary_doc comes directly from the submissions JSON 'primaryDocument' field,
    so no separate index fetch is needed.
    """
    import html as html_lib
    if not primary_doc:
        logger.debug("No primaryDocument for %s", accession_no)
        return ""
    acc_clean = accession_no.replace("-", "")
    cik_clean = cik.lstrip("0")
    doc_url   = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik_clean}/{acc_clean}/{primary_doc}"

    limiter.wait()
    try:
        resp = session.get(doc_url, timeout=60)
        resp.raise_for_status()
        text = resp.text
    except Exception as exc:
        logger.debug("Filing text fetch failed %s: %s", doc_url, exc)
        return ""

    # Remove blocks whose content is never useful prose: scripts, styles,
    # and the hidden iXBRL section that carries machine-readable XBRL metadata.
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>",   " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<ix:hidden[^>]*>.*?</ix:hidden>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    # Strip remaining tags, decode entities, collapse whitespace.
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500_000]


# ---------------------------------------------------------------------------
# Form 4 parsing
# ---------------------------------------------------------------------------

_CEO_CFO_TITLES = frozenset(["ceo", "cfo", "chief executive", "chief financial"])


def _is_ceo_cfo(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _CEO_CFO_TITLES)


def _parse_form4_xml(xml_text: str) -> list[dict]:
    """Parse Form 4 XML into list of transaction dicts."""
    import xml.etree.ElementTree as ET
    transactions = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return transactions

    insider_name  = (root.findtext(".//rptOwnerName") or "").strip()
    insider_title = (root.findtext(".//officerTitle") or "").strip()

    for txn in root.findall(".//nonDerivativeTransaction"):
        code_el = txn.find(".//transactionCode")
        code    = (code_el.text or "").strip() if code_el is not None else ""
        shares_el = txn.find(".//transactionShares/value")
        price_el  = txn.find(".//transactionPricePerShare/value")
        date_el   = txn.find(".//transactionDate/value")
        own_el    = txn.find(".//directOrIndirectOwnership/value")

        try:
            shares = float(shares_el.text) if shares_el is not None else None
        except (TypeError, ValueError):
            shares = None
        try:
            price = float(price_el.text) if price_el is not None else None
        except (TypeError, ValueError):
            price = None

        date_str = (date_el.text or "").strip() if date_el is not None else None
        own_type = (own_el.text or "D").strip() if own_el is not None else "D"

        txn_type_map = {
            "P": "Purchase", "S": "Sale", "A": "Grant",
            "M": "Exercise", "F": "Tax withholding", "G": "Gift",
        }
        txn_type = txn_type_map.get(code, "Other")
        is_open  = 1 if code in ("P", "S") else 0

        transactions.append({
            "insider_name": insider_name,
            "insider_title": insider_title,
            "transaction_type": txn_type,
            "transaction_code": code,
            "shares": shares,
            "price": price,
            "date": date_str,
            "ownership_type": own_type,
            "is_open_market": is_open,
            "is_ceo_cfo": int(_is_ceo_cfo(insider_title)),
        })

    return transactions


def _flag_cluster_buys(conn: sa.engine.Connection, ticker: str, config: dict) -> None:
    window_days  = config["sec"]["cluster_buy_window_days"]
    min_insiders = config["sec"]["cluster_buy_min_insiders"]
    cutoff = (
        datetime.utcnow() - timedelta(days=config["sec"]["insider_lookback_days"])
    ).strftime("%Y-%m-%d")

    rows = conn.execute(
        sa.select(
            insider_transactions.c.date,
            insider_transactions.c.insider_name,
            insider_transactions.c.shares,
        )
        .where(
            (insider_transactions.c.ticker == ticker)
            & (insider_transactions.c.is_open_market == 1)
            & (insider_transactions.c.transaction_code == "P")
            & (insider_transactions.c.date >= cutoff)
        )
        .order_by(insider_transactions.c.date)
    ).fetchall()

    if len(rows) < min_insiders:
        return

    for i, (date_str, _, _) in enumerate(rows):
        if not date_str:
            continue
        win_start = datetime.strptime(date_str[:10], "%Y-%m-%d")
        win_end   = win_start + timedelta(days=window_days)

        insiders_in_window = set()
        total_shares = 0.0
        for d, name, sh in rows[i:]:
            if not d:
                continue
            dt = datetime.strptime(d[:10], "%Y-%m-%d")
            if dt > win_end:
                break
            insiders_in_window.add(name)
            total_shares += sh or 0

        if len(insiders_in_window) >= min_insiders:
            conn.execute(
                insert_or_replace(conn, insider_cluster_flags).values(
                    ticker=ticker,
                    window_start=win_start.strftime("%Y-%m-%d"),
                    window_end=win_end.strftime("%Y-%m-%d"),
                    insider_count=len(insiders_in_window),
                    total_shares=total_shares,
                )
            )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_sec_data(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
    forms: list[str] | None = None,
) -> dict[str, int]:
    """Fetch SEC filings for all tickers. Returns {ticker: filings_stored}."""
    if forms is None:
        forms = config["sec"]["forms"]

    # One-time backfill: rows written before fetched_at was tracked get filed_date.
    conn.execute(
        sa.update(sec_filings)
        .where(sec_filings.c.fetched_at == None)  # noqa: E711
        .values(fetched_at=sec_filings.c.filed_date)
    )
    conn.commit()

    session   = _make_session(config)
    limiter   = _RateLimiter(_MIN_INTERVAL)
    cik_cache: dict[str, str] = {}
    summary:   dict[str, int] = {}

    lookback_cfg = config["sec"].get("form_lookback_days", {})
    default_lookback = config["sec"].get("insider_lookback_days", 180)
    form_cutoffs: dict[str, str] = {}
    for form in forms:
        days = lookback_cfg.get(form, default_lookback)
        form_cutoffs[form] = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Pre-load data to avoid per-ticker queries and redundant HTTP calls
    # ------------------------------------------------------------------
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # 1. Extract CIKs from already-stored filing URLs (avoids HTTP lookup for known tickers)
    stored_ciks: dict[str, str] = {}
    for row in conn.execute(
        sa.select(sec_filings.c.ticker, sec_filings.c.filing_url)
        .where(sec_filings.c.ticker.in_(tickers))
        .where(sec_filings.c.filing_url.isnot(None))
    ).fetchall():
        if row[0] not in stored_ciks:
            m = re.search(r"/edgar/data/(\d+)/", row[1] or "")
            if m:
                stored_ciks[row[0]] = m.group(1)

    # 2. Bulk-load all existing accession numbers (one query instead of one per ticker)
    existing_accs: dict[str, set[str]] = {}
    for ticker_db, acc in conn.execute(
        sa.select(sec_filings.c.ticker, sec_filings.c.accession_no)
        .where(sec_filings.c.ticker.in_(tickers))
    ).fetchall():
        existing_accs.setdefault(ticker_db, set()).add(acc)

    # 3. Skip submissions fetch for tickers already checked today
    checked_today: set[str] = set(conn.execute(
        sa.select(sec_filings.c.ticker).distinct()
        .where(sec_filings.c.fetched_at >= today)
        .where(sec_filings.c.ticker.in_(tickers))
    ).scalars().all())

    skipped = len(checked_today)
    logger.info(
        "SEC EDGAR ingestion for %d tickers, forms=%s — %d skipped (checked today)",
        len(tickers), forms, skipped,
    )

    for ticker in tickers:
        if ticker in checked_today:
            summary[ticker] = 0
            continue

        stored = 0

        # Use CIK from stored URLs first; only hit EDGAR if unknown
        cik = stored_ciks.get(ticker) or cik_cache.get(ticker)
        if not cik:
            cik = _ticker_to_cik(session, ticker, limiter, cik_cache)
        if not cik:
            logger.debug("No CIK found for %s — skipping", ticker)
            summary[ticker] = 0
            continue

        subs = _get_submissions(session, cik, limiter)
        recent = subs.get("filings", {}).get("recent", {})

        form_types    = recent.get("form", [])
        filed_dates   = recent.get("filingDate", [])
        accession_nos = recent.get("accessionNumber", [])
        primary_docs  = recent.get("primaryDocument", [""] * len(form_types))

        fetched_acc = existing_accs.get(ticker, set())

        for ft, fd, acc, pdoc in zip(form_types, filed_dates, accession_nos, primary_docs, strict=False):
            acc_norm = acc.replace("-", "")
            if ft not in forms:
                continue
            if acc in fetched_acc or acc_norm in fetched_acc:
                continue
            if fd < form_cutoffs.get(ft, "1900-01-01"):
                continue

            content = ""
            filing_url = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik.lstrip('0')}/{acc_norm}/"

            if ft in ("10-K", "10-Q", "8-K"):
                content = _fetch_filing_text(session, cik, acc, pdoc or "", limiter)
            elif ft == "4":
                limiter.wait()
                # primaryDocument for Form 4 is always the HTML renderer, not XML.
                # The ownershipDocument XML is embedded in the full SGML .txt file.
                txt_url = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik.lstrip('0')}/{acc_norm}/{acc}.txt"
                try:
                    r = session.get(txt_url, timeout=30)
                    xml_text = _extract_form4_xml(r.text) if r.ok else ""
                except Exception:
                    xml_text = ""

                content = xml_text
                txns = _parse_form4_xml(xml_text) if xml_text else []
                now_ts = datetime.utcnow().isoformat(timespec="seconds")
                for txn in txns:
                    try:
                        conn.execute(
                            insert_or_ignore(conn, insider_transactions).values(
                                ticker=ticker,
                                insider_name=txn["insider_name"],
                                insider_title=txn["insider_title"],
                                transaction_type=txn["transaction_type"],
                                transaction_code=txn["transaction_code"],
                                shares=txn["shares"],
                                price=txn["price"],
                                date=txn["date"],
                                ownership_type=txn["ownership_type"],
                                is_open_market=txn["is_open_market"],
                                is_ceo_cfo=txn["is_ceo_cfo"],
                                accession_no=acc,
                                fetched_at=now_ts,
                            )
                        )
                    except sa.exc.IntegrityError:
                        pass

            conn.execute(
                insert_or_ignore(conn, sec_filings).values(
                    ticker=ticker,
                    form_type=ft,
                    filed_date=fd,
                    accession_no=acc,
                    filing_url=filing_url,
                    content_text=content or None,
                    fetched_at=datetime.utcnow().isoformat(timespec="seconds"),
                )
            )
            stored += 1
            logger.debug("SEC %s %s %s stored", ticker, ft, fd)

        # Touch the most-recent filing row so subsequent same-day runs skip this ticker.
        now_ts = datetime.utcnow().isoformat(timespec="seconds")
        conn.execute(
            sa.update(sec_filings)
            .where(
                (sec_filings.c.ticker == ticker)
                & (sec_filings.c.fetched_at == sa.select(
                    sa.func.max(sec_filings.c.fetched_at)
                ).where(sec_filings.c.ticker == ticker).scalar_subquery())
            )
            .values(fetched_at=now_ts)
        )
        conn.commit()
        _flag_cluster_buys(conn, ticker, config)
        summary[ticker] = stored

    total = sum(summary.values())
    logger.info("SEC ingestion complete — %d filings stored across %d tickers",
                total, len([v for v in summary.values() if v > 0]))
    return summary
