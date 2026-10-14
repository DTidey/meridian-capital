"""Packet 28 AC3: every analyzer looks up the cache with its configured model."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from analysis import earnings_analyzer, filing_analyzer, insider_analyzer, risk_analyzer

_CFG = {
    "analysis": {
        "openai_model": "gpt-4o",
        "openai_model_cheap": "gpt-4o-mini",
        "analyzer_models": {
            "earnings": "gpt-6-sol",
            "filing": "gpt-6-luna",
            "risk": "gpt-6.1-sol",
            "insider": "gpt-6-luna",
        },
    }
}

# (module, analyzer name, fetch helper to stub, stub return value)
_CASES = [
    (
        earnings_analyzer,
        "earnings",
        "_fetch_transcript",
        {"earnings_date": "2026-07-30", "content": "text"},
    ),
    (
        risk_analyzer,
        "risk",
        "_fetch_10k",
        {"accession_no": "0000320193-25-000079", "content_text": "x"},
    ),
    (insider_analyzer, "insider", "_fetch_transactions", [{"shares": 1}]),
    (
        filing_analyzer,
        "filing",
        "_fetch_fundamentals",
        pd.DataFrame({"period_end": ["2026-06-30"], "revenue": [1.0]}),
    ),
]


@pytest.mark.parametrize("module,name,helper,stub", _CASES, ids=[c[1] for c in _CASES])
def test_cache_lookup_uses_configured_model(module, name, helper, stub):
    cache = MagicMock()
    cache.get.return_value = {"cached": True}  # hit: analyse returns before calling the API
    client = MagicMock()
    with patch.object(module, helper, return_value=stub):
        out = module.analyse(MagicMock(), "AAPL", client, cache, _CFG, "2026-10-14")
    assert out == {"cached": True}
    assert cache.get.call_args.kwargs["model"] == _CFG["analysis"]["analyzer_models"][name]
    assert cache.get.call_args.args[0] == name
    client.chat.assert_not_called()


@pytest.mark.parametrize("module,name,helper,stub", _CASES, ids=[c[1] for c in _CASES])
def test_falls_back_to_default_model(module, name, helper, stub):
    # Without analyzer_models, earnings/risk use openai_model and filing/insider the cheap one
    cfg = {"analysis": {"openai_model": "gpt-4o", "openai_model_cheap": "gpt-4o-mini"}}
    cache = MagicMock()
    cache.get.return_value = {"cached": True}
    with patch.object(module, helper, return_value=stub):
        module.analyse(MagicMock(), "AAPL", MagicMock(), cache, cfg, "2026-10-14")
    expected = "gpt-4o" if name in ("earnings", "risk") else "gpt-4o-mini"
    assert cache.get.call_args.kwargs["model"] == expected
