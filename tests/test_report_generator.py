"""Tests for analysis/report_generator.py."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
from analysis.combined_score import compute_combined_scores
from analysis.db import ai_scores as ai_scores_table, combined_scores as combined_scores_table
from analysis.report_generator import generate_reports, _build_report, _earnings_section, _risk_section
from factors.db import factor_scores as factor_scores_table
from data.db import get_engine, initialise_schema, sp500_universe, earnings_calendar


@pytest.fixture
def tmp_engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tmp_db(tmp_engine):
    conn = tmp_engine.connect()
    yield conn
    conn.close()


def _seed_combined(conn, ticker, direction, score):
    conn.execute(combined_scores_table.insert().values(
        ticker=ticker,
        score_date="2024-06-30",
        quant_composite=score,
        ai_composite=None,
        combined_score=score,
        direction=direction,
        computed_at="2024-06-30T00:00:00+00:00",
    ))
    conn.commit()


def _seed_universe(conn, ticker, name="Test Co", sector="Technology"):
    conn.execute(sp500_universe.insert().values(
        ticker=ticker,
        company_name=name,
        gics_sector=sector,
        gics_sub_industry=sector,
        updated_at="2024-01-01",
    ))
    conn.commit()


class TestGenerateReports:
    def test_returns_empty_when_no_candidates(self, tmp_db, tmp_path):
        paths = generate_reports(tmp_db, "2024-06-30", {}, {}, {}, {},
                                 output_dir=str(tmp_path / "reports"))
        assert paths == []

    def test_writes_file_per_candidate(self, tmp_db, tmp_path):
        _seed_universe(tmp_db, "AAPL", "Apple Inc")
        _seed_universe(tmp_db, "MSFT", "Microsoft Corp")
        _seed_combined(tmp_db, "AAPL", "LONG", 85.0)
        _seed_combined(tmp_db, "MSFT", "SHORT", 15.0)

        out_dir = str(tmp_path / "reports")
        paths = generate_reports(tmp_db, "2024-06-30", {}, {}, {}, {},
                                 output_dir=out_dir)
        assert len(paths) == 2
        fnames = [Path(p).name for p in paths]
        assert "AAPL_long.md" in fnames
        assert "MSFT_short.md" in fnames

    def test_neutral_tickers_not_reported(self, tmp_db, tmp_path):
        _seed_universe(tmp_db, "XYZ")
        _seed_combined(tmp_db, "XYZ", "NEUTRAL", 50.0)
        out_dir = str(tmp_path / "reports")
        paths = generate_reports(tmp_db, "2024-06-30", {}, {}, {}, {},
                                 output_dir=out_dir)
        assert paths == []

    def test_report_contains_ticker_and_direction(self, tmp_db, tmp_path):
        _seed_universe(tmp_db, "NVDA", "NVIDIA Corp")
        _seed_combined(tmp_db, "NVDA", "LONG", 92.0)
        out_dir = str(tmp_path / "reports")
        paths = generate_reports(tmp_db, "2024-06-30", {}, {}, {}, {},
                                 output_dir=out_dir)
        content = Path(paths[0]).read_text()
        assert "NVDA" in content
        assert "LONG" in content

    def test_upcoming_catalyst_included(self, tmp_db, tmp_path):
        _seed_universe(tmp_db, "GOOG", "Alphabet Inc")
        _seed_combined(tmp_db, "GOOG", "LONG", 88.0)
        tmp_db.execute(earnings_calendar.insert().values(
            ticker="GOOG", earnings_date="2024-07-25", eps_estimate=1.85, fetched_at="2024-06-01",
        ))
        tmp_db.commit()

        out_dir = str(tmp_path / "reports")
        paths = generate_reports(tmp_db, "2024-06-30", {}, {}, {}, {},
                                 output_dir=out_dir)
        content = Path(paths[0]).read_text()
        assert "2024-07-25" in content
        assert "1.85" in content

    def test_ai_analysis_sections_included(self, tmp_db, tmp_path):
        _seed_universe(tmp_db, "META", "Meta Platforms")
        _seed_combined(tmp_db, "META", "LONG", 81.0)

        ai_data = {
            "META": {
                "earnings": {
                    "management_confidence": {"score": 8, "reasoning": "Strong tone"},
                    "revenue_guidance": {"score": 7, "reasoning": "Good"},
                    "margin_trajectory": {"score": 8, "reasoning": "Improving"},
                    "competitive_position": {"score": 9, "reasoning": "Dominant"},
                    "risk_factors": {"score": 6, "reasoning": "Some risks"},
                    "capital_allocation": {"score": 7, "reasoning": "Buybacks"},
                    "bull_case": "AI monetisation upside",
                    "bear_case": "Regulatory risk",
                    "key_quotes": ["We are accelerating AI investment"],
                    "one_line_summary": "Positive tone",
                },
                "risk": {
                    "risk_severity": "MEDIUM",
                    "boilerplate_percentage": 30,
                    "material_risks": [
                        {"risk": "Regulatory scrutiny", "severity": "HIGH", "category": "Regulatory"}
                    ],
                    "new_risks": [],
                    "one_line_summary": "Moderate risk",
                },
            }
        }
        out_dir = str(tmp_path / "reports")
        paths = generate_reports(tmp_db, "2024-06-30", ai_data, {}, {}, {},
                                 output_dir=out_dir)
        content = Path(paths[0]).read_text()
        assert "Earnings Call" in content
        assert "Risk Factors" in content
        assert "Regulatory scrutiny" in content


class TestBuildReport:
    def test_basic_structure(self):
        md = _build_report(
            ticker="AAPL", company_name="Apple Inc", direction="LONG",
            combined_score=85.5, sector="Technology", score_date="2024-06-30",
            factors={}, ai={}, catalyst=None, sector_info=None,
        )
        assert "# AAPL" in md
        assert "Apple Inc" in md
        assert "LONG" in md
        assert "85.5" in md

    def test_factor_scores_table(self):
        factors = {"composite_score": 82.0, "momentum_score": 75.0, "quality_score": 90.0}
        md = _build_report(
            ticker="MSFT", company_name="Microsoft", direction="LONG",
            combined_score=82.0, sector="Technology", score_date="2024-06-30",
            factors=factors, ai={}, catalyst=None, sector_info=None,
        )
        assert "Momentum" in md
        assert "75.0" in md


class TestEarningsSection:
    def test_includes_categories(self):
        result = {
            "management_confidence": {"score": 8, "reasoning": "Confident"},
            "revenue_guidance":      {"score": 7, "reasoning": "Raised"},
            "margin_trajectory":     {"score": 6, "reasoning": "Stable"},
            "competitive_position":  {"score": 9, "reasoning": "Leader"},
            "risk_factors":          {"score": 5, "reasoning": "Headwinds"},
            "capital_allocation":    {"score": 8, "reasoning": "Buybacks"},
            "bull_case": "Strong execution",
            "bear_case": "Competition",
            "key_quotes": ["We beat expectations"],
        }
        lines = _earnings_section(result)
        text = "\n".join(lines)
        assert "Management Confidence" in text
        assert "Bull case" in text
        assert "Bear case" in text
        assert "We beat expectations" in text


class TestRiskSection:
    def test_includes_severity_and_risks(self):
        result = {
            "risk_severity": "HIGH",
            "boilerplate_percentage": 25,
            "material_risks": [
                {"risk": "Competition from BigTech", "severity": "HIGH", "category": "Competitive"}
            ],
            "new_risks": ["AI disruption"],
            "one_line_summary": "Elevated risk",
        }
        lines = _risk_section(result)
        text = "\n".join(lines)
        assert "HIGH" in text
        assert "Competition from BigTech" in text
        assert "AI disruption" in text
        assert "25%" in text
