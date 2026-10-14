"""Packet 29: non-trading LLM uses on GPT-6; scoring analyzers pinned until a shadow test."""

from pathlib import Path

import yaml

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_non_trading_models_on_gpt6():
    analysis = _CFG["analysis"]
    assert analysis["openai_model"] == "gpt-6-sol"
    assert analysis["openai_model_cheap"] == "gpt-6-luna"


def test_scoring_analyzers_pinned():
    # Changing these moves AI scores (40% of the combined score); stage 3 needs a shadow test first
    assert _CFG["analysis"]["analyzer_models"] == {
        "earnings": "gpt-4o",
        "filing": "gpt-4o-mini",
        "risk": "gpt-4o",
        "insider": "gpt-4o-mini",
    }


def test_new_models_are_reasoning_and_priced():
    from analysis.cost_tracker import _DEFAULT_PRICING, _pricing
    from analysis.llm import is_reasoning_model

    for model in (_CFG["analysis"]["openai_model"], _CFG["analysis"]["openai_model_cheap"]):
        assert is_reasoning_model(_CFG, model)
        assert _pricing(model) is not _DEFAULT_PRICING
