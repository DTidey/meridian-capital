"""Tests for packet 33: Item 1A heading variants, and how the risk score is shown."""

from __future__ import annotations

from analysis.report_generator import _risk_section
from analysis.risk_analyzer import extract_risk_factors
from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL
from dashboard.ticker_common import ai_score_colour, ai_score_text

_BODY = "Our business faces many company-specific risks. " * 60  # about 2,900 characters


def _doc(*blocks: str) -> str:
    return "\n\n".join(blocks)


class TestItemHeadingVariants:
    def test_item_1_dot_a(self):
        # CLX: "ITEM 1.A. RISK FACTORS"
        md = _doc(
            "Intro", "ITEM 1.A. RISK FACTORS", _BODY, "ITEM 1.B. UNRESOLVED STAFF COMMENTS", "x"
        )
        section = extract_risk_factors(md)
        assert section.startswith("ITEM 1.A. RISK FACTORS")
        assert "UNRESOLVED" not in section

    def test_item_1_parenthesised_a(self):
        # HAL / ICE: "Item 1(a). Risk Factors." ... "Item 1(b)."
        md = _doc("Item 1(a). Risk Factors.", _BODY, "ITEM 1 (B). UNRESOLVED STAFF COMMENTS", "x")
        section = extract_risk_factors(md)
        assert section.startswith("Item 1(a)") and "UNRESOLVED" not in section

    def test_part_i_prefix(self):
        # EXPE: "Part I. Item 1A. Risk Factors" ... "Part I. Item 1B."
        md = _doc("Part I. Item 1A. Risk Factors", _BODY, "Part I. Item 1B. Unresolved", "x")
        section = extract_risk_factors(md)
        assert section.startswith("Part I. Item 1A") and "Unresolved" not in section

    def test_item_10_and_1a_ab_do_not_match(self):
        for heading in ("Item 10. Risk factors for directors", "Item 1AA. Risk factors"):
            assert extract_risk_factors(_doc(heading, _BODY, "Item 11", "x")) is None

    def test_item_1_space_a(self):
        md = _doc("Item 1 A Risk Factors", _BODY, "Item 1 B Unresolved", "x")
        assert extract_risk_factors(md).startswith("Item 1 A")

    def test_item_1a_still_ends_at_item_2(self):
        md = _doc("Item 1A. Risk Factors", _BODY, "Item 2. Properties", "Our offices")
        assert "Our offices" not in extract_risk_factors(md)

    def test_item_1a_not_cut_by_subheading_titles(self):
        # Inside a normal Item 1A section, a "Cybersecurity" subheading is not the end.
        md = _doc("Item 1A. Risk Factors", _BODY, "Cybersecurity", _BODY, "Item 1B. None", "x")
        assert extract_risk_factors(md).count("company-specific") == 120


class TestBareRiskFactorsHeading:
    def test_bare_heading_ends_at_later_item_title(self):
        # MS: "Risk Factors" ... "Cybersecurity" (Item 1C title)
        md = _doc("Business", "Risk Factors", "Market Risk", _BODY, "Cybersecurity", "Policy")
        section = extract_risk_factors(md)
        assert section.startswith("Risk Factors")
        assert "Policy" not in section and "Market Risk" in section

    def test_bare_heading_upper_case_market_risk_end(self):
        # EIX: "RISK FACTORS" ... "QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK"
        md = _doc(
            "RISK FACTORS",
            _BODY,
            "QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK",
            "Rates",
        )
        assert "Rates" not in extract_risk_factors(md)

    def test_mda_title_with_curly_apostrophe_ends_section(self):
        md = _doc(
            "Risk Factors",
            _BODY,
            "Management’s Discussion and Analysis of Financial Condition",
            "Revenue",
        )
        assert "Revenue" not in extract_risk_factors(md)

    def test_bare_heading_without_end_is_not_used(self):
        # A repeated page header "Risk Factors" with no later item title is not a section.
        md = _doc("Risk Factors", _BODY, "Some other heading", _BODY)
        assert extract_risk_factors(md) is None

    def test_item_1a_preferred_over_bare_heading(self):
        md = _doc(
            "Item 1A. Risk Factors",
            _BODY,
            "Item 1B. Unresolved Staff Comments",
            "Risk Factors",
            _BODY + _BODY,
            "Properties",
        )
        assert extract_risk_factors(md).startswith("Item 1A")

    def test_bare_used_when_item_1a_is_only_a_contents_entry(self):
        md = _doc(
            "Item 1A. Risk Factors",
            "Item 1B. Unresolved Staff Comments",
            "Risk Factors",
            _BODY,
            "Properties",
        )
        assert extract_risk_factors(md).startswith("Risk Factors\n\n")

    def test_longest_bare_candidate_wins(self):
        md = _doc("Risk Factors", _BODY, "Properties", "Risk Factors", _BODY + _BODY, "Properties")
        assert extract_risk_factors(md).count("company-specific") == 120

    # Adversarial cases beyond the spec edge cases.
    def test_short_bare_section_rejected(self):
        md = _doc("Risk Factors", "Too short.", "Properties")
        assert extract_risk_factors(md) is None

    def test_cross_reference_sentence_not_a_heading(self):
        sentence = 'See "Risk Factors" and "Forward-Looking Statements" herein for more on risks.'
        md = _doc(sentence, _BODY, "Properties")
        assert extract_risk_factors(md) is None

    def test_risk_factors_summary_is_not_bare_heading(self):
        md = _doc("Risk Factors Summary", _BODY, "Properties")
        assert extract_risk_factors(md) is None

    def test_heading_with_trailing_period(self):
        md = _doc("Risk Factors.", _BODY, "Legal Proceedings.", "x")
        assert extract_risk_factors(md).startswith("Risk Factors.")


class TestReportRiskSection:
    def test_score_shown_and_label_marked_not_scored(self):
        result = {
            "risk_severity": "HIGH",
            "material_risks": [
                {"risk": "a", "severity": "HIGH"},
                {"risk": "b", "severity": "MEDIUM"},
            ],
        }
        text = "\n".join(_risk_section(result))
        assert "**Risk score:** 7.0" in text
        assert "Model's overall rating:** HIGH (not scored)" in text
        assert "Overall severity" not in text

    def test_no_score_when_no_risks(self):
        text = "\n".join(_risk_section({"risk_severity": "LOW", "material_risks": []}))
        assert "**Risk score:** none" in text

    def test_no_score_when_no_valid_severity(self):
        result = {"risk_severity": "HIGH", "material_risks": [{"risk": "a", "severity": "?"}]}
        assert "**Risk score:** none (no scorable" in "\n".join(_risk_section(result))


class TestTickerPageAiScores:
    def test_one_decimal(self):
        assert ai_score_text(6.84) == "6.8"
        assert ai_score_text(7.0) == "7.0"

    def test_missing(self):
        assert ai_score_text(None) == "—"

    def test_colour_uses_1_to_10_scale(self):
        assert ai_score_colour(7.5) == LONG_COL
        assert ai_score_colour(5.0) == NEUTRAL
        assert ai_score_colour(3.0) == SHORT_COL
        assert ai_score_colour(None) == NEUTRAL

    def test_colour_boundaries(self):
        assert ai_score_colour(6.0) == LONG_COL
        assert ai_score_colour(5.99) == NEUTRAL
        assert ai_score_colour(4.0) == SHORT_COL
        assert ai_score_colour(4.01) == NEUTRAL
