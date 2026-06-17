"""Tests for execution/short_check.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import time
from unittest.mock import MagicMock

import pytest

from execution.short_check import is_shortable


def _make_asset(shortable=True, easy_to_borrow=True):
    a = MagicMock()
    a.shortable      = shortable
    a.easy_to_borrow = easy_to_borrow
    return a


class TestIsShortable:
    def test_shortable_and_easy_to_borrow(self, tmp_path):
        client = MagicMock()
        client.get_asset.return_value = _make_asset(shortable=True, easy_to_borrow=True)
        assert is_shortable("AAPL", client, tmp_path) is True

    def test_not_shortable(self, tmp_path):
        client = MagicMock()
        client.get_asset.return_value = _make_asset(shortable=False, easy_to_borrow=True)
        assert is_shortable("GME", client, tmp_path) is False

    def test_not_easy_to_borrow(self, tmp_path):
        client = MagicMock()
        client.get_asset.return_value = _make_asset(shortable=True, easy_to_borrow=False)
        assert is_shortable("AMC", client, tmp_path) is False

    def test_api_error_returns_false(self, tmp_path):
        client = MagicMock()
        client.get_asset.side_effect = Exception("API error")
        assert is_shortable("BADTICKER", client, tmp_path) is False

    def test_cache_hit_skips_api(self, tmp_path):
        cache_dir = tmp_path / "shortable"
        cache_dir.mkdir()
        (cache_dir / "TSLA.json").write_text(
            json.dumps({"shortable": True, "ts": time.time()})
        )
        client = MagicMock()
        result = is_shortable("TSLA", client, tmp_path)
        assert result is True
        client.get_asset.assert_not_called()

    def test_expired_cache_refetches(self, tmp_path):
        cache_dir = tmp_path / "shortable"
        cache_dir.mkdir()
        # ts is 8 days ago → expired
        (cache_dir / "NVDA.json").write_text(
            json.dumps({"shortable": False, "ts": time.time() - 8 * 86400})
        )
        client = MagicMock()
        client.get_asset.return_value = _make_asset(shortable=True, easy_to_borrow=True)
        result = is_shortable("NVDA", client, tmp_path, ttl_days=7)
        assert result is True
        client.get_asset.assert_called_once()
