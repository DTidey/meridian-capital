"""SEC EDGAR EFTS data ingestion — 10-K, 10-Q, 8-K, Form 4 insider transactions."""

import contextlib
import logging
import os
import re
import time
from datetime import datetime, timedelta

import requests
import sqlalchemy as sa

from data._utils import utc_now

from .db import (
    insert_or_ignore,
    insert_or_replace,
    insider_cluster_flags,
    insider_transactions,
    sec_filings,
)

logger = logging.getLogger(__name__)

_EFTS_SEARCH = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_DATA = "https://data.sec.gov"  # JSON APIs (submissions, company facts)
_EDGAR_ARCH = "https://www.sec.gov"  # actual filing documents / archives
_SUBMISSIONS = "https://data.sec.gov/submissions"
_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_CIK_RE = re.compile(r"[0-9]{1,10}")  # ASCII digits only: a CIK goes into URL paths

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


def _clean_cik(value) -> str | None:
    """A CIK as digits without leading zeros, or None when it is not a plain CIK."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    text = str(value).strip()
    if not _CIK_RE.fullmatch(text):
        return None
    return text.lstrip("0") or None


def _load_cik_map(session: requests.Session, limiter: _RateLimiter) -> dict[str, str]:
    """Ticker -> CIK (no leading zeros) from SEC's company_tickers.json.

    Raises on a network failure or a response that is not the expected JSON.
    """
    limiter.wait()
    resp = session.get(_COMPANY_TICKERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data:
        raise ValueError("company_tickers.json: expected a non-empty JSON object")
    cik_map: dict[str, str] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            raise ValueError(f"company_tickers.json: unexpected entry {entry!r:.80}")
        ticker = entry.get("ticker")
        cik = _clean_cik(entry.get("cik_str"))
        if not isinstance(ticker, str) or not ticker or cik is None:
            raise ValueError(f"company_tickers.json: unexpected entry {entry!r:.80}")
        cik_map.setdefault(ticker, cik)
    return cik_map


def _get_filings_efts(
    session: requests.Session, ticker: str, form_type: str, limiter: _RateLimiter, count: int = 5
) -> list[dict]:
    """Search EDGAR full-text search for filings by ticker + form type."""
    limiter.wait()
    params = {
        "q": f'"{ticker}"',
        "dateRange": "custom",
        "startdt": (utc_now() - timedelta(days=365 * 3)).strftime("%Y-%m-%d"),
        "enddt": utc_now().strftime("%Y-%m-%d"),
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
        results.append(
            {
                "accession_no": src.get("accession_no", ""),
                "filed_date": src.get("file_date", ""),
                "form_type": form_type,
            }
        )
    return results


def _get_submissions(session: requests.Session, cik: str, limiter: _RateLimiter) -> dict:
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
    return text[start : end + len("</ownershipDocument>")]


def _fetch_filing_text(
    session: requests.Session, cik: str, accession_no: str, primary_doc: str, limiter: _RateLimiter
) -> str:
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
    doc_url = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik_clean}/{acc_clean}/{primary_doc}"

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
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
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


def _parse_form4_xml(xml_text: str) -> tuple[str | None, list[dict]]:
    """Parse Form 4 XML into (issuer CIK or None, transaction dicts).

    Each transaction's txn_index is its 0-based position among nonDerivativeTransaction elements.
    """
    import xml.etree.ElementTree as ET  # nosec B405

    transactions = []
    try:
        root = ET.fromstring(xml_text)  # nosec B314
    except Exception:
        return None, transactions

    issuer_cik = _clean_cik(root.findtext(".//issuerCik"))
    insider_name = (root.findtext(".//rptOwnerName") or "").strip()
    insider_title = (root.findtext(".//officerTitle") or "").strip()

    for txn_index, txn in enumerate(root.findall(".//nonDerivativeTransaction")):
        code_el = txn.find(".//transactionCode")
        code = (code_el.text or "").strip() if code_el is not None else ""
        shares_el = txn.find(".//transactionShares/value")
        price_el = txn.find(".//transactionPricePerShare/value")
        date_el = txn.find(".//transactionDate/value")
        own_el = txn.find(".//directOrIndirectOwnership/value")

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
            "P": "Purchase",
            "S": "Sale",
            "A": "Grant",
            "M": "Exercise",
            "F": "Tax withholding",
            "G": "Gift",
        }
        txn_type = txn_type_map.get(code, "Other")
        is_open = 1 if code in ("P", "S") else 0

        transactions.append(
            {
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
                "txn_index": txn_index,
            }
        )

    return issuer_cik, transactions


_TXN_FIELDS = (
    "insider_name",
    "insider_title",
    "transaction_type",
    "transaction_code",
    "shares",
    "price",
    "date",
    "ownership_type",
    "is_open_market",
    "is_ceo_cfo",
    "txn_index",
)


def _txn_values(ticker: str, accession_no: str, txn: dict, fetched_at: str) -> dict:
    """insider_transactions column values for one parsed transaction."""
    values = {k: txn[k] for k in _TXN_FIELDS}
    values.update(ticker=ticker, accession_no=accession_no, fetched_at=fetched_at)
    return values


def _flag_cluster_buys(conn: sa.engine.Connection, ticker: str, config: dict) -> None:
    _write_cluster_flags(conn, ticker, config)
    conn.commit()


def _write_cluster_flags(conn: sa.engine.Connection, ticker: str, config: dict) -> None:
    """Upsert the ticker's cluster-buy flags without committing (the rebuild is one transaction)."""
    window_days = config["sec"]["cluster_buy_window_days"]
    min_insiders = config["sec"]["cluster_buy_min_insiders"]
    cutoff = (utc_now() - timedelta(days=config["sec"]["insider_lookback_days"])).strftime(
        "%Y-%m-%d"
    )

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
        win_end = win_start + timedelta(days=window_days)

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


def _fetch_form4_xml(
    session: requests.Session, cik: str, accession_no: str, limiter: _RateLimiter
) -> str:
    """The ownershipDocument XML of a Form 4, or "" when it cannot be fetched."""
    limiter.wait()
    # primaryDocument for Form 4 is always the HTML renderer, not XML.
    # The ownershipDocument XML is embedded in the full SGML .txt file.
    acc_norm = accession_no.replace("-", "")
    txt_url = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik.lstrip('0')}/{acc_norm}/{accession_no}.txt"
    try:
        r = session.get(txt_url, timeout=30)
        return _extract_form4_xml(r.text) if r.ok else ""
    except Exception:
        return ""


def _store_form4_transactions(
    conn: sa.engine.Connection, ticker: str, cik: str, accession_no: str, xml_text: str
) -> bool:
    """Insert the Form 4's transactions if its issuer is the ticker's company.

    Returns False when the filing names another issuer (or none), so nothing was stored.
    """
    if not xml_text:
        return True
    issuer_cik, txns = _parse_form4_xml(xml_text)
    if issuer_cik != cik:
        logger.debug(
            "SEC %s Form 4 %s: issuer CIK %s is not %s — transactions not stored",
            ticker,
            accession_no,
            issuer_cik,
            cik,
        )
        return False
    now_ts = utc_now().isoformat(timespec="seconds")
    for txn in txns:
        with contextlib.suppress(sa.exc.IntegrityError):
            conn.execute(
                insert_or_ignore(conn, insider_transactions).values(
                    **_txn_values(ticker, accession_no, txn, now_ts)
                )
            )
    return True


def _stored_url_ciks(conn: sa.engine.Connection, tickers: list[str]) -> dict[str, str]:
    """Ticker -> CIK in the URL of its most recently filed stored filing."""
    stored_ciks: dict[str, str] = {}
    for ticker, url in conn.execute(
        sa.select(sec_filings.c.ticker, sec_filings.c.filing_url)
        .where(sec_filings.c.ticker.in_(tickers))
        .where(sec_filings.c.filing_url.isnot(None))
        .order_by(sec_filings.c.filed_date.desc().nulls_last())
    ).fetchall():
        if ticker not in stored_ciks:
            m = re.search(r"/edgar/data/(\d+)/", url or "")
            if m:
                stored_ciks[ticker] = m.group(1).lstrip("0")
    return stored_ciks


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

    session = _make_session(config)
    limiter = _RateLimiter(_MIN_INTERVAL)
    try:
        cik_map = _load_cik_map(session, limiter)
    except Exception as exc:
        # No fallback to stored CIKs: those are the stale ones this map replaces.
        logger.error("SEC CIK map (company_tickers.json) failed — nothing stored this run: %s", exc)
        return dict.fromkeys(tickers, 0)

    # One-time backfill: rows written before fetched_at was tracked get filed_date.
    conn.execute(
        sa.update(sec_filings)
        .where(sec_filings.c.fetched_at == None)  # noqa: E711
        .values(fetched_at=sec_filings.c.filed_date)
    )
    conn.commit()

    summary: dict[str, int] = {}
    issuer_skipped = 0

    lookback_cfg = config["sec"].get("form_lookback_days", {})
    default_lookback = config["sec"].get("insider_lookback_days", 180)
    form_cutoffs: dict[str, str] = {}
    for form in forms:
        days = lookback_cfg.get(form, default_lookback)
        form_cutoffs[form] = (utc_now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Pre-load data to avoid per-ticker queries and redundant HTTP calls
    # ------------------------------------------------------------------
    today = utc_now().strftime("%Y-%m-%d")

    # 1. CIKs in stored filing URLs, only to warn when a ticker's CIK has changed
    stored_ciks = _stored_url_ciks(conn, tickers)

    # 2. Bulk-load all existing accession numbers (one query instead of one per ticker)
    existing_accs: dict[str, set[str]] = {}
    for ticker_db, acc in conn.execute(
        sa.select(sec_filings.c.ticker, sec_filings.c.accession_no).where(
            sec_filings.c.ticker.in_(tickers)
        )
    ).fetchall():
        existing_accs.setdefault(ticker_db, set()).add(acc)

    # 3. Skip submissions fetch for tickers already checked today
    checked_today: set[str] = set(
        conn.execute(
            sa.select(sec_filings.c.ticker)
            .distinct()
            .where(sec_filings.c.fetched_at >= today)
            .where(sec_filings.c.ticker.in_(tickers))
        )
        .scalars()
        .all()
    )

    skipped = len(checked_today)
    logger.info(
        "SEC EDGAR ingestion for %d tickers, forms=%s — %d skipped (checked today)",
        len(tickers),
        forms,
        skipped,
    )

    for ticker in tickers:
        if ticker in checked_today:
            summary[ticker] = 0
            continue

        stored = 0

        cik = cik_map.get(ticker)
        if not cik:
            logger.warning("No CIK for %s in company_tickers.json — skipping", ticker)
            summary[ticker] = 0
            continue
        old_cik = stored_ciks.get(ticker)
        if old_cik and old_cik != cik:
            logger.warning(
                "SEC CIK for %s changed: stored filings use %s, company_tickers.json maps %s",
                ticker,
                old_cik,
                cik,
            )

        subs = _get_submissions(session, cik, limiter)
        recent = subs.get("filings", {}).get("recent", {})

        form_types = recent.get("form", [])
        filed_dates = recent.get("filingDate", [])
        accession_nos = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [""] * len(form_types))

        fetched_acc = existing_accs.get(ticker, set())

        for ft, fd, acc, pdoc in zip(
            form_types, filed_dates, accession_nos, primary_docs, strict=False
        ):
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
                content = _fetch_form4_xml(session, cik, acc, limiter)
                if not _store_form4_transactions(conn, ticker, cik, acc, content):
                    issuer_skipped += 1

            conn.execute(
                insert_or_ignore(conn, sec_filings).values(
                    ticker=ticker,
                    form_type=ft,
                    filed_date=fd,
                    accession_no=acc,
                    filing_url=filing_url,
                    content_text=content or None,
                    fetched_at=utc_now().isoformat(timespec="seconds"),
                )
            )
            stored += 1
            logger.debug("SEC %s %s %s stored", ticker, ft, fd)

        # Touch the most-recent filing row so subsequent same-day runs skip this ticker.
        now_ts = utc_now().isoformat(timespec="seconds")
        conn.execute(
            sa.update(sec_filings)
            .where(
                (sec_filings.c.ticker == ticker)
                & (
                    sec_filings.c.fetched_at
                    == sa.select(sa.func.max(sec_filings.c.fetched_at))
                    .where(sec_filings.c.ticker == ticker)
                    .scalar_subquery()
                )
            )
            .values(fetched_at=now_ts)
        )
        conn.commit()
        _flag_cluster_buys(conn, ticker, config)
        summary[ticker] = stored

    total = sum(summary.values())
    logger.info(
        "SEC ingestion complete — %d filings stored across %d tickers",
        total,
        len([v for v in summary.values() if v > 0]),
    )
    logger.info(
        "Form 4 issuer check: %d filings name another issuer; their transactions not stored",
        issuer_skipped,
    )
    return summary


# ---------------------------------------------------------------------------
# Rebuild from stored Form 4 XML
# ---------------------------------------------------------------------------

_REBUILD_PAGE = 500
_REBUILD_BATCH = 2000


def _stored_form4s(conn: sa.engine.Connection):
    """Yield (accession_no, xml) for every stored Form 4, one page of rows at a time."""
    last_id = 0
    while True:
        rows = conn.execute(
            sa.select(sec_filings.c.id, sec_filings.c.accession_no, sec_filings.c.content_text)
            .where(sec_filings.c.form_type == "4")
            .where(sec_filings.c.content_text.isnot(None))
            .where(sec_filings.c.content_text != "")
            .where(sec_filings.c.id > last_id)
            .order_by(sec_filings.c.id)
            .limit(_REBUILD_PAGE)
        ).fetchall()
        if not rows:
            return
        yield from ((r.accession_no, r.content_text) for r in rows)
        last_id = rows[-1].id


def _reinsert_transactions(
    conn: sa.engine.Connection, tickers_by_cik: dict[str, list[str]]
) -> tuple[int, int]:
    """Delete all insider rows and flags, then insert every stored Form 4 under each universe
    ticker of its issuer. Returns (filings parsed, filings with no universe issuer)."""
    conn.execute(sa.delete(insider_transactions))
    conn.execute(sa.delete(insider_cluster_flags))
    now_ts = utc_now().isoformat(timespec="seconds")
    parsed = skipped = 0
    batch: list[dict] = []
    for acc, xml_text in _stored_form4s(conn):
        parsed += 1
        issuer_cik, txns = _parse_form4_xml(xml_text)
        owners = tickers_by_cik.get(issuer_cik or "", [])
        if not owners:
            skipped += 1
            continue
        batch.extend(_txn_values(t, acc, txn, now_ts) for t in owners for txn in txns)
        if len(batch) >= _REBUILD_BATCH:
            conn.execute(insert_or_ignore(conn, insider_transactions), batch)
            batch = []
    if batch:
        conn.execute(insert_or_ignore(conn, insider_transactions), batch)
    return parsed, skipped


def rebuild_insider_transactions(
    conn: sa.engine.Connection, tickers: list[str], config: dict
) -> dict[str, int]:
    """Re-create insider_transactions and insider_cluster_flags from stored Form 4 XML.

    One transaction: any failure rolls back and leaves the old rows in place.
    Returns {ticker: rows inserted} for every universe ticker.
    """
    cik_map = _load_cik_map(_make_session(config), _RateLimiter(_MIN_INTERVAL))
    tickers_by_cik: dict[str, list[str]] = {}
    for ticker in tickers:
        if ticker not in cik_map:
            logger.warning("No CIK for %s in company_tickers.json — no insider rows", ticker)
            continue
        tickers_by_cik.setdefault(cik_map[ticker], []).append(ticker)

    try:
        parsed, skipped = _reinsert_transactions(conn, tickers_by_cik)
        for ticker in tickers:
            _write_cluster_flags(conn, ticker, config)
        counts = dict.fromkeys(tickers, 0)
        counts.update(
            conn.execute(
                sa.select(insider_transactions.c.ticker, sa.func.count()).group_by(
                    insider_transactions.c.ticker
                )
            ).fetchall()
        )
        flags = conn.execute(sa.select(sa.func.count()).select_from(insider_cluster_flags)).scalar()
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    logger.info(
        "Insider rebuild: %d Form 4 filings parsed, %d with no universe issuer skipped, "
        "%d rows inserted for %d tickers, %d cluster flags",
        parsed,
        skipped,
        sum(counts.values()),
        sum(1 for n in counts.values() if n),
        flags,
    )
    return counts
