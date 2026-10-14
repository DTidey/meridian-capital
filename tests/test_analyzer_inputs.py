"""Packet 30: filing analyzer column labels and risk analyzer Item 1A extraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import analysis.db  # noqa: F401 — registers tables
from analysis import risk_analyzer
from analysis.cache import AnalysisCache
from analysis.filing_analyzer import _FUND_COLS, _fetch_fundamentals
from analysis.risk_analyzer import _risk_text, extract_risk_factors
from data.db import fundamentals


class TestFilingColumns:
    def test_each_column_holds_its_own_values(self, tmp_db):
        # Distinct value per column, so any mislabelling shows up
        values = {c: float(i + 1) for i, c in enumerate(_FUND_COLS) if c != "period_end"}
        tmp_db.execute(
            fundamentals.insert().values(
                ticker="FIS", period_type="quarterly", period_end="2026-06-30", **values
            )
        )
        tmp_db.commit()
        df = _fetch_fundamentals(tmp_db, "FIS", "2026-10-14")
        row = df.iloc[0]
        for col, expected in values.items():
            assert row[col] == pytest.approx(expected), col
        assert row["period_end"] == "2026-06-30"

    def test_mislabelling_regression_fis(self, tmp_db):
        # FIS 2026-06-30 as stored: the model was told total assets were $494M (it was CFO)
        tmp_db.execute(
            fundamentals.insert().values(
                ticker="FIS",
                period_type="quarterly",
                period_end="2026-06-30",
                total_assets=44_106e6,
                total_equity=16_038e6,
                accounts_receivable=2_413e6,
                cfo=494e6,
            )
        )
        tmp_db.commit()
        row = _fetch_fundamentals(tmp_db, "FIS", "2026-10-14").iloc[0]
        assert row["total_assets"] == pytest.approx(44_106e6)
        assert row["total_equity"] == pytest.approx(16_038e6)
        assert row["accounts_receivable"] == pytest.approx(2_413e6)
        assert row["cfo"] == pytest.approx(494e6)


_TOC = (
    "| Item 1. | Business | 3 |\n|---|---|---|\n| Item 1A. | Risk Factors | 12 |\n"
    "| Item 1B. | Unresolved Staff Comments | 30 |\n| Item 2. | Properties | 31 |"
)


def _doc(*blocks: str) -> str:
    return "\n\n".join(blocks)


class TestExtractRiskFactors:
    def test_plain_heading_to_item_1b(self):
        md = _doc(
            _TOC,
            "Item 1. Business",
            "We make things.",
            "Item 1A. Risk Factors",
            "Supply chain risk is high.",
            "Competition is intense.",
            "Item 1B. Unresolved Staff Comments",
            "None.",
        )
        assert extract_risk_factors(md) == _doc(
            "Item 1A. Risk Factors", "Supply chain risk is high.", "Competition is intense."
        )

    def test_one_row_table_heading(self):
        # AMGN: the heading is a one-row table
        md = _doc(
            _TOC,
            "| Item 1A. | RISK FACTORS |\n|---|---|",
            "Pricing pressure may hurt revenue.",
            "| Item 1B. | UNRESOLVED STAFF COMMENTS |\n|---|---|",
        )
        assert "Pricing pressure may hurt revenue." in extract_risk_factors(md)
        assert "UNRESOLVED" not in extract_risk_factors(md)

    def test_cross_reference_sentence_ignored(self):
        # Adversarial: AMGN's "see Item 1A. Risk Factors for a discussion..." is not a heading
        md = _doc(
            "Item 1A. Risk Factors for a discussion of the factors that could cause results to "
            "differ materially from those in forward-looking statements.",
            "Forward-looking text.",
        )
        assert extract_risk_factors(md) is None

    def test_toc_table_not_a_heading(self):
        assert extract_risk_factors(_doc(_TOC, "Body without sections.")) is None

    def test_ends_at_item_1c_or_2(self):
        md = _doc("ITEM 1A. Risk Factors", "Risk one.", "Item 1C. Cybersecurity", "Cyber text.")
        assert extract_risk_factors(md) == _doc("ITEM 1A. Risk Factors", "Risk one.")
        md2 = _doc("Item 1A. Risk Factors.", "Risk one.", "Item 2. Properties", "HQ.")
        assert extract_risk_factors(md2) == _doc("Item 1A. Risk Factors.", "Risk one.")

    def test_runs_to_end_without_closing_heading(self):
        md = _doc("Item 1A. Risk Factors", "Risk one.", "Risk two.")
        assert extract_risk_factors(md) == md

    def test_longest_candidate_wins(self):
        # Adversarial: a short stray heading earlier must not beat the real section
        md = _doc(
            "Item 1A. Risk Factors",
            "Item 1B. Unresolved Staff Comments",
            "Item 1A. Risk Factors",
            "Real risk one.",
            "Real risk two.",
            "Item 1B. Unresolved Staff Comments",
        )
        assert extract_risk_factors(md) == _doc(
            "Item 1A. Risk Factors", "Real risk one.", "Real risk two."
        )

    def test_empty(self):
        assert extract_risk_factors("") is None


_URL = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/"
_FILING = {
    "accession_no": "0000000001-26-000001",
    "filed_date": "2026-02-01",
    "content_text": "COVER PAGE " * 20,
    "filing_url": _URL,
}


class TestRiskText:
    def test_section_from_edgar_unescaped(self):
        md = _doc(
            "Item 1A. Risk Factors", "Costs rose \\$5 billion \\[est\\].", "y" * 2000, "Item 1B. X"
        )
        with patch("data.edgar_docs.fetch_filing_markdown", return_value=md) as fetch:
            text, source = _risk_text(_FILING, 80_000)
        fetch.assert_called_once_with(_URL, _FILING["accession_no"], "10-K")
        assert source == "edgar"
        assert text == _doc("Item 1A. Risk Factors", "Costs rose $5 billion [est].", "y" * 2000)

    def test_cut_to_max_chars(self):
        md = _doc("Item 1A. Risk Factors", "x" * 5000)
        with patch("data.edgar_docs.fetch_filing_markdown", return_value=md):
            text, _ = _risk_text(_FILING, 100)
        assert len(text) == 100

    def test_fetch_failure_falls_back_to_stored(self, caplog):
        with (
            patch("data.edgar_docs.fetch_filing_markdown", return_value=""),
            caplog.at_level("INFO", logger="analysis.risk_analyzer"),
        ):
            text, source = _risk_text(_FILING, 50)
        assert source == "stored"
        assert text == risk_analyzer._strip_html(_FILING["content_text"])[:50]  # as before
        assert "stored 10-K text" in caplog.text

    def test_no_section_falls_back(self):
        with patch("data.edgar_docs.fetch_filing_markdown", return_value=_doc(_TOC, "Body.")):
            _, source = _risk_text(_FILING, 50)
        assert source == "stored"

    def test_fetch_exception_falls_back(self):
        # Adversarial: a network error must not abort the analysis
        with patch("data.edgar_docs.fetch_filing_markdown", side_effect=RuntimeError("boom")):
            _, source = _risk_text(_FILING, 50)
        assert source == "stored"

    def test_missing_url_skips_fetch(self):
        with patch("data.edgar_docs.fetch_filing_markdown") as fetch:
            _, source = _risk_text({**_FILING, "filing_url": None}, 50)
        fetch.assert_not_called()
        assert source == "stored"


class TestRiskAnalyzerPrompt:
    def test_prompt_contains_section_not_cover(self):
        md = _doc("Item 1A. Risk Factors", "EYLEA sales may erode.", "z" * 2000, "Item 1B. X")
        client = MagicMock()
        client.chat.return_value = {
            "material_risks": [],
            "new_risks": [],
            "boilerplate_percentage": 10,
            "risk_severity": "HIGH",
            "one_line_summary": "x",
        }
        client._tracker._calls = [{"cost_usd": 0.01}]
        cache = MagicMock()
        cache.get.return_value = None
        with (
            patch.object(risk_analyzer, "_fetch_10k", return_value=_FILING),
            patch.object(risk_analyzer, "_fetch_prior_10k", return_value=None),
            patch("data.edgar_docs.fetch_filing_markdown", return_value=md),
        ):
            risk_analyzer.analyse(MagicMock(), "REGN", client, cache, {}, "2026-10-14")
        prompt = client.chat.call_args.args[1]
        assert "EYLEA sales may erode." in prompt
        assert "COVER PAGE" not in prompt


class TestArtifactVersions:
    def test_filing_and_risk_ids_versioned(self):
        assert AnalysisCache.artifact_id_filing("MSFT", "2024-06-30") == "MSFT_2024-06-30_v2"
        assert (
            AnalysisCache.artifact_id_risk("GOOG", "0001234567-24-000001")
            == "GOOG_0001234567-24-000001_v2"
        )

    def test_earnings_and_insider_unchanged(self):
        assert AnalysisCache.artifact_id_earnings("AAPL", "2024-03-31") == "AAPL_2024-03-31"
        assert AnalysisCache.artifact_id_insider("AAPL", "2026-10-13") == "AAPL_2026-10-13"


class TestReviewerEdgeCases:
    def test_em_dash_heading(self):
        md = _doc("Item 1A \u2014 Risk Factors", "Risk one.", "Item 1B \u2014 Unresolved", "x")
        assert extract_risk_factors(md) == _doc("Item 1A \u2014 Risk Factors", "Risk one.")

    def test_item_10_does_not_end_section(self):
        md = _doc("Item 1A. Risk Factors", "Risk one.", "Item 10. Directors", "Risk two.")
        assert "Risk two." in extract_risk_factors(md)

    def test_toc_paragraph_only_falls_back(self):
        # Reviewer repro: the only match is a short contents paragraph, the real heading is split
        md = _doc(
            "Item 1A. Risk Factors 12",
            "Item 1B. Unresolved Staff Comments 30",
            "Item 1A.",
            "Risk Factors",
            "Real risk text. " * 200,
        )
        with patch("data.edgar_docs.fetch_filing_markdown", return_value=md):
            _, source = _risk_text(_FILING, 80_000)
        assert source == "stored"

    def test_short_toc_table_prepended_is_benign(self):
        md = _doc(
            "| Item 1A. | Risk Factors | 12 |\n|---|---|---|",
            "Item 1A. Risk Factors",
            "Real risk text.",
            "Item 1B. Unresolved Staff Comments",
        )
        assert "Real risk text." in extract_risk_factors(md)

    def test_source_logged_on_success(self, caplog):
        md = _doc("Item 1A. Risk Factors", "r" * 3000)
        with (
            patch("data.edgar_docs.fetch_filing_markdown", return_value=md),
            caplog.at_level("INFO", logger="analysis.risk_analyzer"),
        ):
            _, source = _risk_text(_FILING, 80_000)
        assert source == "edgar"
        assert "using Item 1A from EDGAR" in caplog.text
