"""Packet 44 (H5): database tickers vs Alpaca symbols."""

import pytest

from execution.symbols import from_alpaca, to_alpaca


@pytest.mark.parametrize(("db", "alpaca"), [("BRK-B", "BRK.B"), ("BF-B", "BF.B"), ("AAPL", "AAPL")])
def test_round_trip(db, alpaca):
    assert to_alpaca(db) == alpaca
    assert from_alpaca(alpaca) == db
