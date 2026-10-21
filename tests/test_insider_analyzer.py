"""Tests for analysis/insider_analyzer.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401
from analysis.cache import AnalysisCache
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


_NEUTRAL = {
    "signal_strength": "NEUTRAL",
    "confidence": "LOW",
    "key_transactions": [],
    "reasoning": "r",
    "one_line_summary": "s",
}


def _insert_cluster(conn, ticker, window_start, window_end):
    conn.execute(
        insider_cluster_flags.insert().values(
            ticker=ticker,
            window_start=window_start,
            window_end=window_end,
            insider_count=3,
            total_shares=1500.0,
            flagged_at=window_end,
        )
    )
    conn.commit()


def _prompt(client) -> str:
    return client.chat.call_args[0][1]


class TestCacheKey:
    """Packet 37: the key follows the transactions, not the score date."""

    def test_unchanged_inputs_hit_cache_next_day(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0, is_ceo_cfo=1)
        cache = AnalysisCache(tmp_db)
        first = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", first, cache, {}, "2024-06-28")
        second = _fake_client(dict(_NEUTRAL))
        result = analyse(tmp_db, "AAPL", second, cache, {}, "2024-07-01")
        first.chat.assert_called_once()
        second.chat.assert_not_called()
        assert result["signal_strength"] == "NEUTRAL"

    def test_new_transaction_calls_api(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0)
        cache = AnalysisCache(tmp_db)
        analyse(tmp_db, "AAPL", _fake_client(dict(_NEUTRAL)), cache, {}, "2024-06-28")
        _insert_txn(tmp_db, "AAPL", "2024-06-30", 500, 185.0)
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, cache, {}, "2024-07-01")
        client.chat.assert_called_once()

    def test_transaction_leaving_window_calls_api(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-04-01", 1000, 170.0)
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 500, 180.0)
        cache = AnalysisCache(tmp_db)
        analyse(tmp_db, "AAPL", _fake_client(dict(_NEUTRAL)), cache, {}, "2024-06-28")
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, cache, {}, "2024-07-15")  # 2024-04-01 now > 90 days old
        client.chat.assert_called_once()
        assert "2024-04-01" not in _prompt(client)

    def test_cluster_flag_appearing_calls_api(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0)
        cache = AnalysisCache(tmp_db)
        analyse(tmp_db, "AAPL", _fake_client(dict(_NEUTRAL)), cache, {}, "2024-06-28")
        _insert_cluster(tmp_db, "AAPL", "2024-06-01", "2024-06-30")
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, cache, {}, "2024-07-01")
        client.chat.assert_called_once()

    def test_cache_set_uses_key_from_prompt_text(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0)
        cache = _fake_cache()
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, cache, {}, "2024-06-28")
        txn_text = _format_transactions(
            [
                {
                    "name": "John Smith",
                    "title": "Director",
                    "type": "P",
                    "shares": 1000,
                    "price": 180.0,
                    "date": "2024-06-01",
                    "is_ceo_cfo": False,
                }
            ],
            None,
        )
        expected = AnalysisCache.artifact_id_insider("AAPL", txn_text)
        assert cache.get.call_args[0][2] == expected
        assert cache.set.call_args[0][2] == expected
        assert txn_text in _prompt(client)

    def test_prompt_has_no_score_date(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0)
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, _fake_cache(), {}, "2024-06-28")
        prompt = _prompt(client)
        assert "2024-06-28" not in prompt
        assert "2024-03-30" not in prompt  # window start
        assert "Analysis window: 90 days of open-market transactions" in prompt

    def test_same_date_order_is_fixed(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 111, 180.0)
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 222, 180.0)
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, _fake_cache(), {}, "2024-06-28")
        prompt = _prompt(client)
        # SQLite returns ties in id order; Postgres may not, which the id tiebreak guards against
        assert prompt.index("222 shares") < prompt.index("111 shares")  # newer id first

    def test_other_ticker_result_not_reused(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0)
        tmp_db.execute(
            insider_transactions.insert().values(
                ticker="MSFT",
                insider_name="John Smith",
                insider_title="Director",
                transaction_type="P",
                transaction_code="P",
                shares=1000,
                price=180.0,
                date="2024-06-01",
                is_open_market=1,
                is_ceo_cfo=0,
                accession_no="0009999-msft",
                fetched_at="2024-01-01",
            )
        )
        tmp_db.commit()
        cache = AnalysisCache(tmp_db)
        analyse(tmp_db, "AAPL", _fake_client(dict(_NEUTRAL)), cache, {}, "2024-06-28")
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "MSFT", client, cache, {}, "2024-06-28")
        client.chat.assert_called_once()

    def test_model_change_still_misses(self, tmp_db):
        _insert_txn(tmp_db, "AAPL", "2024-06-01", 1000, 180.0)
        cache = AnalysisCache(tmp_db)
        cfg_a = {"analysis": {"analyzer_models": {"insider": "gpt-6-luna"}}}
        cfg_b = {"analysis": {"analyzer_models": {"insider": "gpt-6-sol"}}}
        analyse(tmp_db, "AAPL", _fake_client(dict(_NEUTRAL)), cache, cfg_a, "2024-06-28")
        client = _fake_client(dict(_NEUTRAL))
        analyse(tmp_db, "AAPL", client, cache, cfg_b, "2024-07-01")
        client.chat.assert_called_once()


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
