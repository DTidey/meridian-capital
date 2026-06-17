"""Tests for analysis/insider_analyzer.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401
from analysis.insider_analyzer import _format_transactions, _validate, analyse, insider_score
from data.db import get_engine, initialise_schema, insider_cluster_flags, insider_transactions


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


def _fake_cache():
    cache = MagicMock()
    cache.get.return_value = None
    return cache


def _fake_client(response: dict):
    client = MagicMock()
    client.chat.return_value = response
    client._tracker._calls = [{"cost_usd": 0.002}]
    return client


def _insert_txn(conn, ticker, date, shares, price, txn_type="P", is_ceo_cfo=0):
    conn.execute(
        insider_transactions.insert().values(
            ticker=ticker,
            insider_name="John Smith",
            insider_title="CEO" if is_ceo_cfo else "Director",
            transaction_type=txn_type,
            transaction_code="P",
            shares=shares,
            price=price,
            date=date,
            is_open_market=1,
            is_ceo_cfo=is_ceo_cfo,
            accession_no=f"0001234-{date}-{shares}",
            fetched_at="2024-01-01",
        )
    )
    conn.commit()


class TestNoTransactions:
    def test_returns_none_when_no_transactions(self, tmp_db):
        cache = _fake_cache()
        client = _fake_client({})
        result = analyse(tmp_db, "AAPL", client, cache, {}, "2024-06-30")
        assert result is None
        client.chat.assert_not_called()

    def test_returns_none_when_all_not_open_market(self, tmp_db):
        conn = tmp_db
        conn.execute(
            insider_transactions.insert().values(
                ticker="AAPL",
                insider_name="X",
                insider_title="CFO",
                transaction_type="S",
                transaction_code="S",
                shares=100,
                price=150.0,
                date="2024-06-01",
                is_open_market=0,
                is_ceo_cfo=1,
                accession_no="0001-01-01-100",
                fetched_at="2024-01-01",
            )
        )
        conn.commit()
        result = analyse(conn, "AAPL", _fake_client({}), _fake_cache(), {}, "2024-06-30")
        assert result is None


class TestCacheHit:
    def test_returns_cached_without_api_call(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0, is_ceo_cfo=1)
        cached_result = {
            "signal_strength": "MODERATE_BUY",
            "confidence": "HIGH",
            "key_transactions": [],
            "reasoning": "cached",
            "one_line_summary": "ok",
        }
        cache = _fake_cache()
        cache.get.return_value = cached_result
        client = _fake_client({})
        result = analyse(tmp_db, "AAPL", client, cache, {}, "2024-06-30")
        assert result == cached_result
        client.chat.assert_not_called()


class TestApiCall:
    def test_calls_api_and_caches_result(self, tmp_db):
        _insert_txn(tmp_db, "MSFT", "2024-06-10", 5000, 420.0, is_ceo_cfo=1)
        api_result = {
            "signal_strength": "STRONG_BUY",
            "confidence": "HIGH",
            "key_transactions": ["CEO bought 5000 shares at $420"],
            "reasoning": "Strong conviction buy",
            "one_line_summary": "CEO open-market purchase signals high conviction",
        }
        cache = _fake_cache()
        client = _fake_client(api_result)
        result = analyse(tmp_db, "MSFT", client, cache, {}, "2024-06-30")
        assert result["signal_strength"] == "STRONG_BUY"
        client.chat.assert_called_once()
        cache.set.assert_called_once()

    def test_uses_cheap_model_by_default(self, tmp_db):
        _insert_txn(tmp_db, "GOOG", "2024-06-15", 200, 175.0)
        api_result = {
            "signal_strength": "NEUTRAL",
            "confidence": "LOW",
            "key_transactions": [],
            "reasoning": "r",
            "one_line_summary": "s",
        }
        client = _fake_client(api_result)
        config = {"analysis": {"openai_model_cheap": "gpt-4o-mini"}}
        analyse(tmp_db, "GOOG", client, _fake_cache(), config, "2024-06-30")
        _, kwargs = client.chat.call_args
        assert kwargs.get("model") == "gpt-4o-mini"

    def test_respects_analyzer_model_override(self, tmp_db):
        _insert_txn(tmp_db, "NVDA", "2024-06-20", 300, 900.0, is_ceo_cfo=1)
        api_result = {
            "signal_strength": "MODERATE_BUY",
            "confidence": "MEDIUM",
            "key_transactions": [],
            "reasoning": "r",
            "one_line_summary": "s",
        }
        client = _fake_client(api_result)
        config = {"analysis": {"analyzer_models": {"insider": "gpt-4o"}}}
        analyse(tmp_db, "NVDA", client, _fake_cache(), config, "2024-06-30")
        _, kwargs = client.chat.call_args
        assert kwargs.get("model") == "gpt-4o"

    def test_outside_lookback_window_returns_none(self, tmp_db):
        _insert_txn(tmp_db, "TSLA", "2024-01-01", 1000, 200.0)  # > 90 days before score_date
        result = analyse(tmp_db, "TSLA", _fake_client({}), _fake_cache(), {}, "2024-06-30")
        assert result is None

    def test_cluster_flag_included_in_prompt(self, tmp_db):
        _insert_txn(tmp_db, "META", "2024-06-15", 500, 500.0)
        tmp_db.execute(
            insider_cluster_flags.insert().values(
                ticker="META",
                window_start="2024-06-01",
                window_end="2024-06-30",
                insider_count=4,
                total_shares=2000.0,
                flagged_at="2024-06-30",
            )
        )
        tmp_db.commit()
        api_result = {
            "signal_strength": "STRONG_BUY",
            "confidence": "HIGH",
            "key_transactions": [],
            "reasoning": "r",
            "one_line_summary": "s",
        }
        client = _fake_client(api_result)
        analyse(tmp_db, "META", client, _fake_cache(), {}, "2024-06-30")
        _, kwargs = client.chat.call_args
        assert (
            "CLUSTER FLAG" in kwargs.get("user_prompt", "")
            or "CLUSTER FLAG" in client.chat.call_args[0][1]
        )


class TestInsiderScore:
    def test_strong_buy_gives_10(self):
        assert insider_score({"signal_strength": "STRONG_BUY"}) == 10.0

    def test_strong_sell_gives_1(self):
        assert insider_score({"signal_strength": "STRONG_SELL"}) == 1.0

    def test_neutral_gives_5(self):
        assert insider_score({"signal_strength": "NEUTRAL"}) == 5.0

    def test_moderate_buy_gives_7_5(self):
        assert insider_score({"signal_strength": "MODERATE_BUY"}) == 7.5

    def test_none_returns_none(self):
        assert insider_score(None) is None


class TestFormatTransactions:
    def test_ceo_cfo_flagged(self):
        txns = [
            {
                "name": "Jane CEO",
                "title": "CEO",
                "type": "P",
                "shares": 1000,
                "price": 150.0,
                "date": "2024-06-01",
                "is_ceo_cfo": True,
            }
        ]
        text = _format_transactions(txns, None)
        assert "[CEO/CFO]" in text

    def test_cluster_appended(self):
        txns = [
            {
                "name": "X",
                "title": "Dir",
                "type": "P",
                "shares": 100,
                "price": 10.0,
                "date": "2024-06-01",
                "is_ceo_cfo": False,
            }
        ]
        cluster = {
            "insider_count": 3,
            "total_shares": 1000.0,
            "window_start": "2024-06-01",
            "window_end": "2024-06-30",
        }
        text = _format_transactions(txns, cluster)
        assert "CLUSTER FLAG" in text
        assert "3 insiders" in text


class TestValidate:
    def test_invalid_signal_defaults_to_neutral(self):
        result = _validate({"signal_strength": "VERY_BULLISH"})
        assert result["signal_strength"] == "NEUTRAL"

    def test_valid_signal_unchanged(self):
        result = _validate({"signal_strength": "STRONG_BUY", "confidence": "HIGH"})
        assert result["signal_strength"] == "STRONG_BUY"

    def test_invalid_confidence_defaults_to_medium(self):
        result = _validate({"signal_strength": "NEUTRAL", "confidence": "VERY_HIGH"})
        assert result["confidence"] == "MEDIUM"
