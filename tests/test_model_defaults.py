"""Packet 35: code-level model defaults match config.yaml; no stale gpt-4o fallbacks."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from analysis.llm import DEFAULT_MODEL, DEFAULT_MODEL_CHEAP

_ROOT = Path(__file__).resolve().parent.parent
_CFG = yaml.safe_load((_ROOT / "config.yaml").read_text())
_SOURCE_DIRS = [
    "analysis",
    "reporting",
    "dashboard",
    "data",
    "factors",
    "portfolio",
    "risk",
    "execution",
]
# Model names written as string literals, e.g. "gpt-4o" or 'gpt-4o-mini'
_GPT4O_LITERAL = re.compile(r"""["']gpt-4o""")


def _source_files():
    files = [p for d in _SOURCE_DIRS for p in (_ROOT / d).rglob("*.py")]
    return files + sorted(_ROOT.glob("run_*.py"))


class TestDefaultsMatchConfig:
    def test_default_model(self):
        assert _CFG["analysis"]["openai_model"] == DEFAULT_MODEL

    def test_default_cheap_model(self):
        assert _CFG["analysis"]["openai_model_cheap"] == DEFAULT_MODEL_CHEAP


class TestNoStaleLiterals:
    def test_no_gpt4o_literals_outside_price_table(self):
        offenders = []
        for path in _source_files():
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if not _GPT4O_LITERAL.search(line):
                    continue
                # The price table keeps gpt-4o rows (stored results and tests use them), and
                # unknown models are deliberately priced as gpt-4o so the cost ceiling errs high
                if path.name == "cost_tracker.py" and (
                    re.match(r'\s+"gpt-4o(-mini)?": \{', line)
                    or line.startswith('_DEFAULT_PRICING = _PRICING["gpt-4o"]')
                ):
                    continue
                offenders.append(f"{path.relative_to(_ROOT)}:{n}: {line.strip()}")
        assert offenders == []


class TestLazyImports:
    def test_modules_do_not_load_openai_at_import(self):
        import subprocess
        import sys

        code = (
            "import sys; import analysis.cost_tracker, reporting.commentary, reporting.lp_letter,"
            " dashboard.jarvis;"
            "print('openai' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "False"


class TestCeilingDefault:
    def test_would_exceed_ceiling_default_uses_unknown_model_price(self):
        from analysis.cost_tracker import CostTracker

        tracker = CostTracker(ceiling_usd=2.50)
        # 1M tokens at the unknown-model (gpt-4o) input price of $2.50 reaches the ceiling
        assert tracker.would_exceed_ceiling(1_000_000)
        assert not tracker.would_exceed_ceiling(999_000)
