"""Earnings transcript ingestion — FMP API with SEC 8-K Exhibit 99 fallback."""

import html as html_lib
import logging
import os
import re
import time
from datetime import datetime, timedelta

import requests
import sqlalchemy as sa

from .db import _dialect_insert, earnings_transcripts, insert_or_ignore, sec_filings

logger = logging.getLogger(__name__)

_EDGAR_ARCH   = "https://www.sec.gov"
_SEC_INTERVAL = 1.0 / 8   # 8 req/s fair-use limit

# Phrases that appear in earnings content (transcripts AND press releases).
# Requiring ≥ 3 hits guards against unrelated 8-K exhibits.
_EARNINGS_MARKERS = [
    # Call transcript markers
    "operator", "question-and-answer", "q&a", "conference call", "earnings call",
    "good morning", "good afternoon", "good evening", "thank you for standing by",
    # Press release / results markers
    "financial results", "quarterly results", "fiscal quarter",
    "revenue", "earnings per share", "diluted eps",
    "operating income", "year-over-year", "guidance",
]


# ---------------------------------------------------------------------------
# FMP
# ---------------------------------------------------------------------------

def _fetch_transcript_fmp(ticker: str, fmp_key: str, base_url: str) -> list[dict] | None:
    """Fetch latest earnings transcripts from FMP.

    Returns:
        list[dict]  — transcripts found (may be empty)
        None        — plan-level block (402/403); caller should stop using FMP
    """
    url = f"{base_url}/earning_call_transcript/{ticker}"
    try:
        resp = requests.get(url, params={"apikey": fmp_key}, timeout=30)
    except Exception as exc:
        logger.debug("FMP transcript request failed %s: %s", ticker, exc)
        return []

    if resp.status_code == 402:
        logger.warning("FMP transcripts require a paid plan (402) — falling back to 8-K exhibit mining")
        return None
    if resp.status_code == 403:
        body = resp.json() if resp.content else {}
        logger.warning("FMP transcript endpoint blocked (403): %s — falling back to 8-K exhibit mining",
                       body.get("Error Message", resp.text[:80]))
        return None
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("FMP transcript fetch failed %s: %s", ticker, exc)
        return []

    if not isinstance(data, list) or not data:
        return []

    results = []
    for item in data[:3]:
        results.append({
            "earnings_date": item.get("date", "")[:10],
            "quarter":       f"Q{item.get('quarter', '')}",
            "year":          item.get("year"),
            "content":       item.get("content", ""),
        })
    return results


# ---------------------------------------------------------------------------
# 8-K Exhibit 99 fallback
# ---------------------------------------------------------------------------

def _sec_get(session: requests.Session, url: str) -> requests.Response | None:
    """Rate-limited GET against SEC EDGAR."""
    time.sleep(_SEC_INTERVAL)
    try:
        resp = session.get(url, timeout=30)
        return resp if resp.ok else None
    except Exception as exc:
        logger.debug("SEC GET failed %s: %s", url, exc)
        return None


def _find_exhibit_99(session: requests.Session, cik: str, acc: str) -> str | None:
    """Return the URL of the first Exhibit 99.x file in an 8-K filing, or None."""
    acc_clean = acc.replace("-", "")
    index_url = f"{_EDGAR_ARCH}/Archives/edgar/data/{cik}/{acc_clean}/{acc}-index.htm"
    resp = _sec_get(session, index_url)
    if not resp:
        return None

    # Each <tr> that contains "EX-99" holds a href to the exhibit file.
    # EDGAR returns absolute paths: /Archives/edgar/data/{cik}/{acc_clean}/{filename}
    for row in re.split(r"</tr>", resp.text, flags=re.IGNORECASE):
        if "EX-99" not in row.upper():
            continue
        hrefs = re.findall(r'href="([^"]+\.htm[l]?)"', row, re.IGNORECASE)
        # Accept absolute archive paths; skip SEC site-navigation links
        for h in hrefs:
            if h.startswith("/Archives/"):
                return f"{_EDGAR_ARCH}{h}"
            if not h.startswith("/") and not h.startswith("http"):
                return f"{_EDGAR_ARCH}/Archives/edgar/data/{cik}/{acc_clean}/{h}"

    return None


def _fetch_and_clean(session: requests.Session, url: str) -> str:
    """Download an exhibit and return cleaned plain text."""
    resp = _sec_get(session, url)
    if not resp:
        return ""
    text = resp.text
    text = re.sub(r"<script[^>]*>.*?</script>",   " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>",      " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<ix:hidden[^>]*>.*?</ix:hidden>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500_000]


def _looks_like_earnings_content(text: str) -> bool:
    """True if this exhibit contains earnings-related content."""
    if len(text) < 3_000:
        return False
    text_lower = text.lower()
    return sum(1 for m in _EARNINGS_MARKERS if m in text_lower) >= 3


def _quarter_from_date(date_str: str) -> tuple[str, int]:
    """Estimate (quarter_label, year) from filing date.

    Filing month → quarter: Jan–Feb → Q4 prior year, Mar–May → Q1,
    Jun–Aug → Q2, Sep–Nov → Q3.
    """
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return "", 0
    m = dt.month
    if m <= 2:
        return "Q4", dt.year - 1
    elif m <= 5:
        return "Q1", dt.year
    elif m <= 8:
        return "Q2", dt.year
    else:
        return "Q3", dt.year


def _fetch_transcript_8k(
    conn: sa.engine.Connection,
    ticker: str,
    http_session: requests.Session,
) -> list[dict]:
    """Mine Exhibit 99 files from 8-K filings not yet checked for transcript content.

    Sets sec_filings.transcript_checked = True for every filing processed, so it is
    never re-fetched regardless of whether qualifying content was found.
    """
    rows = conn.execute(
        sa.select(
            sec_filings.c.accession_no,
            sec_filings.c.filed_date,
            sec_filings.c.filing_url,
        )
        .where(
            (sec_filings.c.ticker    == ticker)
            & (sec_filings.c.form_type == "8-K")
            & sec_filings.c.filing_url.isnot(None)
            & sa.or_(
                sec_filings.c.transcript_checked == False,  # noqa: E712
                sec_filings.c.transcript_checked.is_(None),
            )
        )
        .order_by(sec_filings.c.filed_date.desc(), sec_filings.c.accession_no.desc())
        .limit(4)
    ).fetchall()

    results = []
    for acc, filed_date, filing_url in rows:
        m = re.search(r"/edgar/data/(\d+)/", filing_url or "")
        if not m or not acc:
            _mark_checked(conn, acc)
            continue
        cik = m.group(1)

        exhibit_url = _find_exhibit_99(http_session, cik, acc)
        if not exhibit_url:
            _mark_checked(conn, acc)
            continue

        text = _fetch_and_clean(http_session, exhibit_url)
        _mark_checked(conn, acc)

        if not _looks_like_earnings_content(text):
            continue

        quarter, year = _quarter_from_date(filed_date or "")
        results.append({
            "earnings_date":       (filed_date or "")[:10],
            "quarter":             quarter,
            "year":                year,
            "content":             text,
            "source_accession_no": acc,
        })
        logger.debug("8-K exhibit found for %s (%s %s)", ticker, quarter, year)

    return results


def _mark_checked(conn: sa.engine.Connection, accession_no: str | None) -> None:
    if not accession_no:
        return
    conn.execute(
        sa.update(sec_filings)
        .where(sec_filings.c.accession_no == accession_no)
        .values(transcript_checked=True)
    )


# ---------------------------------------------------------------------------
# Shared storage helper
# ---------------------------------------------------------------------------

def _store_entries(conn: sa.engine.Connection, ticker: str,
                   entries: list[dict], now: str) -> int:
    """Store transcript entries. Sentinels (content=None) are stored to mark
    accession numbers as checked so they are never re-fetched."""
    stored = 0
    ins = _dialect_insert(conn)(earnings_transcripts)
    for entry in entries:
        if not entry.get("earnings_date") or not entry.get("content"):
            continue
        try:
            # On conflict keep existing content; always update source_accession_no.
            conn.execute(
                ins.values(
                    ticker=ticker,
                    earnings_date=entry["earnings_date"],
                    quarter=entry.get("quarter"),
                    year=entry.get("year"),
                    content=entry["content"],
                    fetched_at=now,
                    source_accession_no=entry.get("source_accession_no"),
                ).on_conflict_do_update(
                    index_elements=["ticker", "earnings_date"],
                    set_={"source_accession_no": ins.excluded.source_accession_no},
                )
            )
            stored += 1
        except Exception as exc:
            logger.debug("Transcript insert error %s: %s", ticker, exc)
    return stored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_transcripts(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
) -> dict[str, int]:
    """Fetch earnings transcripts/results for given tickers.

    Primary source: FMP API (if FMP_API_KEY is set and the plan supports it).
    Fallback:       Exhibit 99 files from 8-K filings already in sec_filings.
    """
    fmp_key  = os.getenv("FMP_API_KEY", "").strip()
    base_url = config["transcripts"]["fmp_base_url"]
    now      = datetime.utcnow().isoformat(timespec="seconds")
    summary: dict[str, int] = {}

    fmp_active = bool(fmp_key)
    if not fmp_active:
        logger.info("Transcripts: no FMP_API_KEY — using 8-K exhibit fallback")

    one_year_ago = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")

    # Tickers that file 8-Ks far more often than quarterly (> 20/year) are not
    # earnings-reporting via 8-K (e.g. daily ASX buy-back filings). Bulk-mark all
    # their unchecked 8-Ks as checked so they are never retried.
    heavy_filers = set(conn.execute(
        sa.select(sec_filings.c.ticker)
        .where(
            sa.and_(
                sec_filings.c.form_type == "8-K",
                sec_filings.c.ticker.in_(tickers),
                sec_filings.c.filed_date >= one_year_ago,
            )
        )
        .group_by(sec_filings.c.ticker)
        .having(sa.func.count() > 20)
    ).scalars().all())
    if heavy_filers:
        logger.info(
            "Transcripts: bulk-skipping %d heavy 8-K filers (>20/yr): %s",
            len(heavy_filers), ", ".join(sorted(heavy_filers)),
        )
        conn.execute(
            sa.update(sec_filings)
            .where(
                sa.and_(
                    sec_filings.c.ticker.in_(heavy_filers),
                    sec_filings.c.form_type == "8-K",
                    sa.or_(
                        sec_filings.c.transcript_checked == False,  # noqa: E712
                        sec_filings.c.transcript_checked.is_(None),
                    ),
                )
            )
            .values(transcript_checked=True)
        )
        conn.commit()

    # Tickers with no unchecked 8-K filings need no EDGAR requests at all.
    tickers_with_unchecked: set[str] = set(conn.execute(
        sa.select(sec_filings.c.ticker).distinct()
        .where(
            sa.and_(
                sec_filings.c.form_type == "8-K",
                sec_filings.c.ticker.in_(tickers),
                sec_filings.c.filed_date >= one_year_ago,
                sec_filings.c.filing_url.isnot(None),
                sa.or_(
                    sec_filings.c.transcript_checked == False,  # noqa: E712
                    sec_filings.c.transcript_checked.is_(None),
                ),
            )
        )
    ).scalars().all())

    # Tickers whose transcripts were already fetched today need no external call.
    today = datetime.utcnow().strftime("%Y-%m-%d")
    fetched_today: set[str] = set(conn.execute(
        sa.select(earnings_transcripts.c.ticker).distinct()
        .where(
            sa.and_(
                earnings_transcripts.c.ticker.in_(tickers),
                earnings_transcripts.c.fetched_at >= today,
            )
        )
    ).scalars().all())

    to_fetch = [t for t in tickers if t in tickers_with_unchecked and t not in fetched_today]
    skipped  = len(tickers) - len(to_fetch)
    logger.info(
        "Transcripts: %d tickers to fetch, %d skipped (all filings already checked)",
        len(to_fetch), skipped,
    )

    http_session = requests.Session()
    agent = os.getenv("SEC_USER_AGENT", "MeridianCapital/1.0")
    email = os.getenv("SEC_USER_EMAIL", "unknown@example.com")
    http_session.headers["User-Agent"] = f"{agent} {email}"

    for ticker in to_fetch:
        entries: list[dict] = []

        if fmp_active:
            result = _fetch_transcript_fmp(ticker, fmp_key, base_url)
            if result is None:
                fmp_active = False
                logger.info("Transcripts: switching to 8-K exhibit fallback for remaining tickers")
                entries = _fetch_transcript_8k(conn, ticker, http_session)
            elif result:
                entries = result
            else:
                entries = _fetch_transcript_8k(conn, ticker, http_session)
        else:
            entries = _fetch_transcript_8k(conn, ticker, http_session)

        stored = _store_entries(conn, ticker, entries, now)
        summary[ticker] = stored

    conn.commit()
    total = sum(summary.values())
    logger.info("Transcripts complete — %d stored across %d tickers",
                total, len([v for v in summary.values() if v > 0]))
    return summary
