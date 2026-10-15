"""Packets 29 and 34: LLM uses on GPT-6; scoring analyzer models pinned (shadow test first)."""

from pathlib import Path

import yaml

_CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


def test_non_trading_models_on_gpt6():
    analysis = _CFG["analysis"]
    assert analysis["openai_model"] == "gpt-6-sol"
    assert analysis["openai_model_cheap"] == "gpt-6-luna"


def test_scoring_analyzers_pinned():
    # Changing these moves AI scores (40% of the combined score); a change needs a shadow test
    # first (packet 34 ran one before moving them to GPT-6)
    assert _CFG["analysis"]["analyzer_models"] == {
        "earnings": "gpt-6-sol",
        "filing": "gpt-6-luna",
        "risk": "gpt-6-sol",
        "insider": "gpt-6-luna",
    }


def test_new_models_are_reasoning_and_priced():
    from analysis.cost_tracker import _DEFAULT_PRICING, _pricing
    from analysis.llm import is_reasoning_model

    analysis = _CFG["analysis"]
    models = {analysis["openai_model"], analysis["openai_model_cheap"]}
    models |= set(analysis["analyzer_models"].values())
    for model in models:
        assert is_reasoning_model(_CFG, model)
        assert _pricing(model) is not _DEFAULT_PRICING
