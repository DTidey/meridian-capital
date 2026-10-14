"""Tests for dashboard/ticker_research.py — packet 18 AC5, AC6, AC7."""

from __future__ import annotations

from dashboard.ticker_research import (
    analysis_markdown,
    md_escape,
    parse_result,
    release_title,
    safe_sec_url,
)


class TestMdEscape:
    def test_dollar_amounts_not_latex(self):
        assert md_escape("sold @ $334.05 and $332.26") == r"sold @ \$334.05 and \$332.26"

    def test_markdown_and_html_chars_escaped(self):
        out = md_escape("[click](http://x) **bold** <b>`x`</b> # h | t _i_")
        for ch in "[]*<>`#|_":
            assert f"\\{ch}" in out
        assert "[" not in out.replace("\\[", "")

    def test_backslash_escaped_first(self):
        # Adversarial: a literal backslash must not cancel the next escape
        assert md_escape("\\$") == "\\\\\\$"

    def test_none_is_empty(self):
        assert md_escape(None) == ""


class TestAnalysisMarkdown:
    def test_score_reasoning_dimension(self):
        md = analysis_markdown(
            {"management_confidence": {"score": 9.0, "reasoning": "Strong."}, "bull_case": "Up."}
        )
        assert "**Management confidence** (9): Strong." in md
        assert "**Bull case:** Up." in md

    def test_one_line_summary_left_out(self):
        assert "Summary" not in analysis_markdown({"one_line_summary": "Summary"})

    def test_string_list_bulleted(self):
        md = analysis_markdown({"red_flags": ["Debt rising", "Low CFO"]})
        assert "**Red flags**" in md
        assert "- Debt rising" in md
        assert "- Low CFO" in md

    def test_list_of_dicts(self):
        md = analysis_markdown(
            {"material_risks": [{"risk": "Supply chain", "severity": "HIGH", "category": "Ops"}]}
        )
        assert "- Supply chain — HIGH — Ops" in md

    def test_dimension_without_score(self):
        md = analysis_markdown({"risk_factors": {"reasoning": "Few."}})
        assert "**Risk factors**: Few." in md

    def test_llm_text_is_escaped(self):
        # Adversarial: injected markdown links from model output render literally
        md = analysis_markdown({"reasoning": "[win](http://evil) $5"})
        assert "\\[win\\]" in md
        assert "\\$5" in md

    def test_model_keys_escaped(self):
        # Adversarial: keys come from the model too
        md = analysis_markdown({"[x](http://evil)": "v"})
        assert "\\[x\\]" in md

    def test_bool_score_not_rendered_as_number(self):
        md = analysis_markdown({"dim": {"score": True, "reasoning": "r"}})
        assert "(1)" not in md

    def test_scalar_values(self):
        md = analysis_markdown({"boilerplate_percentage": 30, "risk_level": "MEDIUM"})
        assert "**Boilerplate percentage:** 30" in md
        assert "**Risk level:** MEDIUM" in md


class TestParseResult:
    def test_valid_object(self):
        assert parse_result('{"a": 1}') == {"a": 1}

    def test_invalid_json_is_none(self):
        assert parse_result("not json") is None
        assert parse_result(None) is None

    def test_non_object_is_none(self):
        # Adversarial: a JSON list or string is not an analysis result
        assert parse_result("[1, 2]") is None
        assert parse_result('"text"') is None


class TestSafeSecUrl:
    def test_sec_url_kept(self):
        url = "https://www.sec.gov/Archives/edgar/data/320193/000114036126039158/"
        assert safe_sec_url(url) == url

    def test_other_hosts_rejected(self):
        assert safe_sec_url("https://evil.example/x") is None
        assert safe_sec_url("http://www.sec.gov/x") is None
        assert safe_sec_url(None) is None

    def test_lookalike_host_rejected(self):
        # Adversarial: prefix match on host must not accept sec.gov.evil.com
        assert safe_sec_url("https://www.sec.gov.evil.com/x") is None
        assert safe_sec_url("javascript:alert(1)") is None


class TestReleaseTitle:
    def test_title(self):
        row = {"quarter": "Q2", "year": 2026, "earnings_date": "2026-07-30"}
        assert release_title(row) == "Earnings release - Q2 2026 (2026-07-30)"

    def test_missing_quarter(self):
        row = {"quarter": None, "year": None, "earnings_date": "2026-07-30"}
        assert release_title(row) == "Earnings release (2026-07-30)"
