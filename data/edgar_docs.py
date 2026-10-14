"""Fetch a filing's documents from EDGAR on demand and convert their HTML to markdown."""

from __future__ import annotations

import logging
import os
import re
import warnings

import requests
from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

logger = logging.getLogger(__name__)

_SEC = "https://www.sec.gov"
_ARCHIVES = f"{_SEC}/Archives/"
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_MAX_SPAN = 50
_BLOCKS = ["p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]
_MD_SPECIAL = re.compile(r"([\\`*_\[\]#<>|$~])")
_OPENERS = {"$", "(", "($", "$(", "€", "£"}
_CLOSERS = {")", "%", ")%", "%)"}

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def _escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def _session() -> requests.Session:
    s = requests.Session()
    agent = os.getenv("SEC_USER_AGENT", "MeridianCapital/1.0")
    email = os.getenv("SEC_USER_EMAIL", "unknown@example.com")
    s.headers.update({"User-Agent": f"{agent} {email}"})
    return s


def filing_documents(
    session, filing_url: str | None, accession: str, form_type: str
) -> list[tuple[str, str]]:
    """(type, URL) of the main document, plus EX-99 exhibits for an 8-K."""
    if (
        not filing_url
        or not filing_url.startswith(_ARCHIVES)
        or not _ACCESSION.fullmatch(accession)
    ):
        return []
    index_url = f"{filing_url.rstrip('/')}/{accession}-index.htm"
    try:
        resp = session.get(index_url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("EDGAR index fetch failed %s: %s", index_url, exc)
        return []

    table = BeautifulSoup(resp.text, "lxml").find("table", class_="tableFile")
    if table is None:
        return []
    docs = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        link = tr.find("a", href=True)
        if len(cells) < 4 or link is None:
            continue
        doc_type = cells[3].get_text(strip=True)
        url = _archive_url(link["href"])
        if url is None:
            continue
        is_main = not docs and doc_type == form_type
        is_release = form_type == "8-K" and doc_type.startswith("EX-99")
        if is_main or is_release:
            docs.append((doc_type, url))
    return docs


def _archive_url(href: str) -> str | None:
    href = href.removeprefix("/ix?doc=")
    return f"{_SEC}{href}" if href.startswith("/Archives/") else None


def fetch_filing_markdown(filing_url: str | None, accession: str, form_type: str) -> str:
    """The filing's main document (and an 8-K's EX-99 exhibits) as markdown; "" on failure."""
    session = _session()
    parts = []
    for i, (doc_type, url) in enumerate(
        filing_documents(session, filing_url, accession, form_type)
    ):
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            md = html_to_markdown(resp.text)
        except Exception as exc:
            logger.warning("EDGAR document fetch failed %s: %s", url, exc)
            md = ""
        if not md and i == 0:
            return ""  # without the main document the filing can't be shown
        md = md or "*Couldn't fetch this exhibit from EDGAR.*"
        parts.append(f"## {_escape(doc_type)}\n\n{md}" if form_type == "8-K" else md)
    return "\n\n---\n\n".join(parts)


def html_to_markdown(html: str) -> str:
    """Readable markdown from filing HTML: paragraphs kept, tables rebuilt, text escaped."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header", "ix:hidden"]):
        tag.decompose()

    tables = []
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None or table.parent is None:
            continue
        tables.append(_table_markdown(table))
        table.replace_with(NavigableString(f"\n\x00T{len(tables) - 1}\x00\n"))
    for tag in soup.find_all(_BLOCKS):
        tag.insert_after("\n")

    out = []
    for line in soup.get_text().split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        placeholder = re.fullmatch(r"\x00T(\d+)\x00", line)
        if placeholder:
            line = tables[int(placeholder.group(1))]
        elif line:
            line = _escape(line)
        if line:
            out.append(line)
    return "\n\n".join(out)


def _cell_text(cell) -> str:
    text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
    return re.sub(r"\(\s+", "(", re.sub(r"\s+([)%])", r"\1", text))


def _span(cell, attr: str) -> int:
    try:
        return max(1, min(int(cell.get(attr, 1)), _MAX_SPAN))
    except ValueError:
        return 1


def _grid(table) -> list[list[tuple[str, int]]]:
    """Rows of (text, cell id); a spanned cell's text is copied into every position it covers."""
    rows = []
    carried: dict[int, tuple[int, tuple[str, int]]] = {}  # column -> (rows left, cell)
    for cell_id_base, tr in enumerate(table.find_all("tr")):
        row: list[tuple[str, int]] = []
        cells = iter(tr.find_all(["td", "th"]))
        for k, cell in enumerate(cells):
            while len(row) in carried:
                row.append(_take_carried(carried, len(row)))
            entry = (_cell_text(cell), cell_id_base * 1000 + k)
            down = _span(cell, "rowspan")
            for _ in range(_span(cell, "colspan")):
                if down > 1:
                    carried[len(row)] = (down - 1, entry)
                row.append(entry)
        while len(row) in carried:
            row.append(_take_carried(carried, len(row)))
        if any(text for text, _ in row):
            rows.append(_attach_symbols(row))
    width = max((len(r) for r in rows), default=0)
    for r in rows:
        r += [("", -1 - i) for i in range(width - len(r))]
    return rows


def _take_carried(carried: dict, col: int) -> tuple[str, int]:
    left, entry = carried.pop(col)
    if left > 1:
        carried[col] = (left - 1, entry)
    return entry


def _attach_symbols(row: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Within a row, join $ and ( to the next value and ) and % to the previous one."""
    texts = [t for t, _ in row]
    ids = [cid for _, cid in row]

    def join(target: int, source: int, before: bool) -> None:
        joined = texts[source] + texts[target] if before else texts[target] + texts[source]
        for k in range(len(texts)):  # every copy of a spanned cell
            if ids[k] == ids[target]:
                texts[k] = joined
            elif ids[k] == ids[source]:
                texts[k] = ""

    for i in range(len(texts)):
        if texts[i] in _OPENERS:
            nxt = next((j for j in range(i + 1, len(texts)) if texts[j] and ids[j] != ids[i]), None)
            if nxt is not None and texts[nxt] not in _OPENERS | _CLOSERS:
                join(nxt, i, before=True)
        elif texts[i] in _CLOSERS:
            prev = next((j for j in range(i - 1, -1, -1) if texts[j] and ids[j] != ids[i]), None)
            if prev is not None:
                join(prev, i, before=False)
    return list(zip(texts, ids, strict=True))


def _covered(grid, col: int, other: int) -> bool:
    """True if every non-empty cell in col equals the cell beside it in other."""
    return all(not r[col][0] or r[col][0] == r[other][0] for r in grid)


def _kept_columns(grid) -> list[int]:
    """Drop empty columns and columns that only repeat a neighbour (spanned headers)."""
    cols = list(range(len(grid[0]))) if grid else []
    kept: list[int] = []
    for c in cols:
        if not (kept and _covered(grid, c, kept[-1])) and any(r[c][0] for r in grid):
            kept.append(c)
    final: list[int] = []
    for c in reversed(kept):
        if not (final and _covered(grid, c, final[-1])):
            final.append(c)
    return final[::-1]


def _table_markdown(table) -> str:
    """Markdown table from an EDGAR table; spanned text shown once per row."""
    grid = _grid(table)
    cols = _kept_columns(grid)
    if not cols:
        return ""
    rows, shown = [], set()
    for r in grid:
        row = []
        for c in cols:
            text, cid = r[c]
            row.append("" if cid in shown else _escape(text))
            if text:
                shown.add(cid)
        rows.append(row)
    if len(cols) == 1:
        return "\n\n".join(r[0] for r in rows if r[0])
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)
