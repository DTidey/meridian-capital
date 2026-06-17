"""Provider selection based on environment variables."""

import pytest

from data.providers import (
    FundamentalsProvider,
    MacroProvider,
    PriceProvider,
    Providers,
    TranscriptProvider,
)


def test_no_keys_all_fallback(monkeypatch):
    for key in ("POLYGON_API_KEY", "FMP_API_KEY", "FRED_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    p = Providers()
    assert p.prices == PriceProvider.YFINANCE
    assert p.fundamentals == FundamentalsProvider.YFINANCE
    assert p.macro == MacroProvider.NONE
    assert p.transcripts == TranscriptProvider.NONE
    assert not p.has_polygon
    assert not p.has_fmp
    assert not p.has_fred


def test_polygon_key_routes_prices(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test-polygon-key")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    p = Providers()
    assert p.prices == PriceProvider.POLYGON
    assert p.has_polygon
    assert p.fundamentals == FundamentalsProvider.YFINANCE


def test_fmp_key_routes_transcripts_only(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    p = Providers()
    assert p.transcripts == TranscriptProvider.FMP
    assert p.fundamentals == FundamentalsProvider.YFINANCE  # FMP key does not affect fundamentals
    assert p.has_fmp
    assert p.prices == PriceProvider.YFINANCE


def test_fred_key_routes_macro(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    p = Providers()
    assert p.macro == MacroProvider.FRED
    assert p.has_fred


def test_empty_string_key_treated_as_absent(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "   ")
    monkeypatch.setenv("FMP_API_KEY", "")
    p = Providers()
    assert p.prices == PriceProvider.YFINANCE
    assert p.fundamentals == FundamentalsProvider.YFINANCE
    assert not p.has_polygon
    assert not p.has_fmp


def test_all_keys_present(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "poly")
    monkeypatch.setenv("FMP_API_KEY", "fmp")
    monkeypatch.setenv("FRED_API_KEY", "fred")
    p = Providers()
    assert p.prices == PriceProvider.POLYGON
    assert p.fundamentals == FundamentalsProvider.YFINANCE  # FMP key never routes fundamentals
    assert p.macro == MacroProvider.FRED
    assert p.transcripts == TranscriptProvider.FMP
