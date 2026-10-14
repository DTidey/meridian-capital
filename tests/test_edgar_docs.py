"""Tests for data/edgar_docs.py — packet 20 AC1, AC2."""

from __future__ import annotations

from data.edgar_docs import filing_documents, html_to_markdown

_FOLDER = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/"
_ACC = "0000320193-26-000018"

_INDEX_8K = """
<html><body><table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td>
 <td><a href="/ix?doc=/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm">
 aapl-20260730.htm</a> iXBRL</td><td>8-K</td><td>40000</td></tr>
<tr><td>2</td><td>EX-99.1</td>
 <td><a href="/Archives/edgar/data/320193/000032019326000018/a8-kex991.htm">a8-kex991.htm</a>
 </td><td>EX-99.1</td><td>170000</td></tr>
<tr><td>3</td><td>EX-99.2</td>
 <td><a href="/Archives/edgar/data/320193/000032019326000018/a8-kex992.htm">a8-kex992.htm</a>
 </td><td>EX-99.2</td><td>9000</td></tr>
<tr><td>4</td><td></td>
 <td><a href="/Archives/edgar/data/320193/000032019326000018/g1.jpg">g1.jpg</a></td>
 <td>GRAPHIC</td><td>9000</td></tr>
</table></body></html>
"""


class _Resp:
    def __init__(self, text, ok=True):
        self.text = text
        self.ok = ok
        self.content = text.encode()

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("HTTP error")


class _Session:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.pages.get(url, _Resp("", ok=False))


_INDEX_URL = f"{_FOLDER}{_ACC}-index.htm"


class TestFilingDocuments:
    def test_8k_main_document_and_ex99_exhibits(self):
        session = _Session({_INDEX_URL: _Resp(_INDEX_8K)})
        docs = filing_documents(session, _FOLDER, _ACC, "8-K")
        assert docs == [
            ("8-K", f"{_FOLDER}aapl-20260730.htm"),
            ("EX-99.1", f"{_FOLDER}a8-kex991.htm"),
            ("EX-99.2", f"{_FOLDER}a8-kex992.htm"),
        ]

    def test_10q_main_document_only(self):
        index = _INDEX_8K.replace(">8-K<", ">10-Q<").replace("EX-99", "EX-31")
        session = _Session({_INDEX_URL: _Resp(index)})
        docs = filing_documents(session, _FOLDER, _ACC, "10-Q")
        assert docs == [("10-Q", f"{_FOLDER}aapl-20260730.htm")]

    def test_folder_without_trailing_slash(self):
        session = _Session({_INDEX_URL: _Resp(_INDEX_8K)})
        assert filing_documents(session, _FOLDER.rstrip("/"), _ACC, "8-K")
        assert session.urls == [_INDEX_URL]

    def test_off_site_hrefs_ignored(self):
        # Adversarial: a crafted index must not send requests off sec.gov
        index = _INDEX_8K.replace(
            "/ix?doc=/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm",
            "https://evil.example/x.htm",
        ).replace("/Archives/edgar/data/320193/000032019326000018/a8-kex991.htm", "//evil/x.htm")
        session = _Session({_INDEX_URL: _Resp(index)})
        urls = [u for _, u in filing_documents(session, _FOLDER, _ACC, "8-K")]
        assert urls == [f"{_FOLDER}a8-kex992.htm"]

    def test_non_sec_filing_url_not_fetched(self):
        # Adversarial: the stored filing URL itself is validated before use
        session = _Session({})
        assert filing_documents(session, "https://evil.example/folder/", _ACC, "8-K") == []
        assert session.urls == []

    def test_malformed_accession_not_fetched(self):
        session = _Session({})
        assert filing_documents(session, _FOLDER, "../../x", "8-K") == []
        assert session.urls == []

    def test_missing_index_returns_empty(self):
        assert filing_documents(_Session({}), _FOLDER, _ACC, "8-K") == []

    def test_index_without_table_returns_empty(self):
        session = _Session({_INDEX_URL: _Resp("<html><body>Not found</body></html>")})
        assert filing_documents(session, _FOLDER, _ACC, "8-K") == []


_INCOME_TABLE = """
<table>
<tr><td></td><td colspan="3">Three Months Ended</td><td></td><td colspan="3">Nine Months</td></tr>
<tr><td></td><td colspan="2">June 27, 2026</td><td></td><td></td>
    <td colspan="2">June 27, 2026</td><td></td></tr>
<tr><td>Products</td><td>$</td><td>78,678</td><td></td><td></td><td>$</td><td>272,629</td>
    <td></td></tr>
<tr><td>Services</td><td colspan="2">30,739</td><td></td><td></td><td colspan="2">91,728</td>
    <td></td></tr>
<tr><td>Other, net</td><td>(</td><td>171</td><td>)</td><td></td><td colspan="2">670</td>
    <td></td></tr>
<tr><td>Margin</td><td colspan="2">50.1</td><td>%</td><td></td><td colspan="2">47.0</td>
    <td>%</td></tr>
</table>
"""


class TestHtmlToMarkdown:
    def test_financial_table_aligned(self):
        md = html_to_markdown(f"<html><body>{_INCOME_TABLE}</body></html>")
        rows = [r for r in md.splitlines() if r.startswith("|")]
        assert "| Products | \\$78,678 | \\$272,629 |" in rows
        assert "| Services | 30,739 | 91,728 |" in rows
        assert "| Other, net | (171) | 670 |" in rows
        assert "| Margin | 50.1% | 47.0% |" in rows
        assert rows[1].startswith("|---|")

    def test_paragraphs_separated(self):
        md = html_to_markdown("<body><p>First para.</p><div>Second <b>bold</b> para.</div></body>")
        assert md == "First para.\n\nSecond bold para."

    def test_hidden_xbrl_and_scripts_dropped(self):
        html = (
            "<body><ix:header><ix:hidden>aapl-20250927 false 2025 FY</ix:hidden></ix:header>"
            "<script>alert(1)</script><style>p{}</style><p>Visible</p></body>"
        )
        assert html_to_markdown(html) == "Visible"

    def test_single_column_layout_table_is_prose(self):
        html = "<body><table><tr><td>Exhibit 99.1</td></tr><tr><td>Apple reports</td></tr></table>"
        assert html_to_markdown(html) == "Exhibit 99.1\n\nApple reports"

    def test_text_is_escaped(self):
        # Adversarial: filing text can't inject markdown links, HTML or LaTeX
        md = html_to_markdown("<body><p>Revenue $1 and $2 [x](http://e) &lt;b&gt;</p></body>")
        assert md == "Revenue \\$1 and \\$2 \\[x\\](http://e) \\<b\\>"

    def test_pipes_in_cells_escaped(self):
        md = html_to_markdown("<body><table><tr><td>a|b</td><td>1</td></tr></table></body>")
        assert "| a\\|b | 1 |" in md

    def test_huge_colspan_capped(self):
        # Adversarial: a hostile colspan must not build a giant grid
        html = '<body><table><tr><td colspan="100000">x</td><td>1</td></tr></table></body>'
        md = html_to_markdown(html)
        assert len(md) < 200

    def test_bad_colspan_treated_as_one(self):
        html = '<body><table><tr><td colspan="wide">x</td><td>1</td></tr></table></body>'
        assert "| x | 1 |" in html_to_markdown(html)

    def test_nested_table_only_outer_converted(self):
        html = (
            "<body><table><tr><td>Outer</td><td><table><tr><td>in1</td><td>in2</td></tr>"
            "</table></td></tr></table></body>"
        )
        md = html_to_markdown(html)
        assert sum(line.startswith("|---") for line in md.splitlines()) == 1
        assert "in1" in md

    def test_empty_document(self):
        assert html_to_markdown("") == ""


class TestTableLayouts:
    def test_dollar_only_on_some_rows_keeps_headers_over_their_years(self):
        # Reviewer's repro: "$" only on some rows must not shift "2026" over the labels
        html = (
            "<body><table>"
            "<tr><td></td><td colspan=3>2026</td><td></td><td colspan=3>2025</td></tr>"
            "<tr><td>Net sales</td><td>$</td><td>94,036</td><td></td><td></td><td>$</td>"
            "<td>85,777</td><td></td></tr>"
            "<tr><td>Cost</td><td></td><td>(5,000</td><td>)</td><td></td><td></td>"
            "<td>(4,000</td><td>)</td></tr>"
            "</table></body>"
        )
        rows = [r for r in html_to_markdown(html).splitlines() if r.startswith("|")]
        assert rows[0] == "|  | 2026 | 2025 |"
        assert rows[2] == "| Net sales | \\$94,036 | \\$85,777 |"
        assert rows[3] == "| Cost | (5,000) | (4,000) |"

    def test_rowspan_label_keeps_year_row_aligned(self):
        html = (
            "<body><table>"
            "<tr><td rowspan=2>(millions)</td><td colspan=2>Three Months</td></tr>"
            "<tr><td>2026</td><td>2025</td></tr>"
            "<tr><td>Revenue</td><td>114,529</td><td>79,477</td></tr>"
            "</table></body>"
        )
        rows = [r for r in html_to_markdown(html).splitlines() if r.startswith("|")]
        assert rows[0] == "| (millions) | Three Months |  |"
        assert rows[2] == "|  | 2026 | 2025 |"
        assert rows[3] == "| Revenue | 114,529 | 79,477 |"

    def test_percent_on_spanned_value(self):
        html = (
            '<body><table><tr><td>Margin</td><td colspan="2">50.1</td><td>%</td></tr>'
            "<tr><td>Other</td><td>$</td><td>7</td><td></td></tr></table></body>"
        )
        rows = [r for r in html_to_markdown(html).splitlines() if r.startswith("|")]
        assert rows == ["| Margin | 50.1% |", "|---|---|", "| Other | \\$7 |"]

    def test_huge_rowspan_capped(self):
        # Adversarial: rowspan is capped like colspan
        html = '<body><table><tr><td rowspan="999999">x</td><td>1</td></tr></table></body>'
        assert "| x | 1 |" in html_to_markdown(html)


class TestFetchFilingMarkdown:
    def _patch(self, monkeypatch, pages):
        from data import edgar_docs

        monkeypatch.setattr(edgar_docs, "_session", lambda: _Session(pages))

    def test_8k_with_exhibit(self, monkeypatch):
        from data.edgar_docs import fetch_filing_markdown

        self._patch(
            monkeypatch,
            {
                _INDEX_URL: _Resp(_INDEX_8K),
                f"{_FOLDER}aapl-20260730.htm": _Resp("<p>Cover</p>"),
                f"{_FOLDER}a8-kex991.htm": _Resp("<p>Results</p>"),
                f"{_FOLDER}a8-kex992.htm": _Resp("<p>Slides</p>"),
            },
        )
        md = fetch_filing_markdown(_FOLDER, _ACC, "8-K")
        assert md.split("\n\n---\n\n") == [
            "## 8-K\n\nCover",
            "## EX-99.1\n\nResults",
            "## EX-99.2\n\nSlides",
        ]

    def test_failed_exhibit_noted(self, monkeypatch):
        from data.edgar_docs import fetch_filing_markdown

        self._patch(
            monkeypatch,
            {
                _INDEX_URL: _Resp(_INDEX_8K),
                f"{_FOLDER}aapl-20260730.htm": _Resp("<p>Cover</p>"),
                f"{_FOLDER}a8-kex992.htm": _Resp("<p>Slides</p>"),
            },
        )
        md = fetch_filing_markdown(_FOLDER, _ACC, "8-K")
        assert "## EX-99.1\n\n*Couldn't fetch this exhibit from EDGAR.*" in md

    def test_failed_main_document_is_failure(self, monkeypatch):
        # Adversarial: exhibits alone must not pass for the filing
        from data.edgar_docs import fetch_filing_markdown

        self._patch(
            monkeypatch,
            {_INDEX_URL: _Resp(_INDEX_8K), f"{_FOLDER}a8-kex991.htm": _Resp("<p>Results</p>")},
        )
        assert fetch_filing_markdown(_FOLDER, _ACC, "8-K") == ""
