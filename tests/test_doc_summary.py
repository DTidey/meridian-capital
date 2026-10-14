"""Tests for analysis/doc_summary.py — packet 20 AC5, AC6, AC7, AC8."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from analysis import doc_summary
from analysis.doc_summary import (
    clean_summary,
    stream_summary,
    summary_messages,
    truncate_tokens,
    unverified_figures,
)


class TestPrompts:
    def test_8k_prompt(self):
        system = summary_messages("8-K", "text")[0]["content"]
        assert "guidance" in system
        assert "exactly as" in system
        assert "links" in system

    def test_periodic_prompt(self):
        system = summary_messages("10-K", "text")[0]["content"]
        assert "risk factors" in system.lower()
        assert "liquidity" in system.lower()

    def test_source_in_user_message(self):
        assert summary_messages("10-Q", "THE SOURCE")[1] == {
            "role": "user",
            "content": "THE SOURCE",
        }

    def test_unknown_form_uses_periodic_prompt(self):
        assert summary_messages("S-1", "x")[0] == summary_messages("10-K", "x")[0]


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate_tokens("hello world", 100) == ("hello world", False)

    def test_long_text_cut(self):
        text = "word " * 5_000
        cut, was_cut = truncate_tokens(text, 1_000)
        assert was_cut
        assert len(cut) < len(text)
        assert text.startswith(cut)


class TestUnverifiedFigures:
    SOURCE = "Revenue of $109.4 billion, net sales 109,417 and margin 50.1 percent in 2026."

    def test_verbatim_figures_pass(self):
        assert unverified_figures("Revenue $109.4 billion; margin 50.1%.", self.SOURCE) == []

    def test_separator_differences_ignored(self):
        assert unverified_figures("Net sales were 109417 million.", self.SOURCE) == []

    def test_invented_figure_flagged(self):
        assert unverified_figures("Net sales 110,000 and 12.5% growth.", self.SOURCE) == [
            "110,000",
            "12.5",
        ]

    def test_years_and_single_digits_ignored(self):
        assert unverified_figures("In fiscal 2027, Q3 had 2 segments.", self.SOURCE) == []

    def test_partial_number_match_not_accepted(self):
        # Adversarial: "09,4" style fragments must not match inside a longer number
        assert unverified_figures("Units of 941.", "Value 109,417.") == ["941"]

    def test_integer_prefix_of_decimal_not_accepted(self):
        # Adversarial: "12" must not be verified by the source's "12.5"
        assert unverified_figures("Growth of 12%.", "Growth of 12.5%.") == ["12"]

    def test_trailing_comma_trimmed(self):
        assert unverified_figures("Sales of 1,234, up.", "none") == ["1,234"]

    def test_each_flagged_once(self):
        assert unverified_figures("777 and 777", self.SOURCE) == ["777"]


class TestCleanSummary:
    def test_dollars_escaped(self):
        assert clean_summary("Revenue $109.4B to $120B") == "Revenue \\$109.4B to \\$120B"

    def test_links_and_images_become_text(self):
        out = clean_summary("See [the release](https://evil.example) ![img](http://x/y.png)")
        assert out == "See the release img"

    def test_reference_links_and_autolinks(self):
        text = "See [site][1] and <https://evil.example/t.png>\n[1]: https://evil.example"
        out = clean_summary(text)
        assert "[1]" not in out
        assert "<https" not in out
        assert "evil.example/t.png" in out  # shown as plain text, not a link target

    def test_double_backslash_dollar_escaped(self):
        assert clean_summary("\\\\$5") == "\\\\\\$5"

    def test_already_escaped_dollar_not_doubled(self):
        assert clean_summary("\\$5") == "\\$5"


class _Chunk(SimpleNamespace):
    pass


def _chunk(text=None, usage=None):
    choices = [SimpleNamespace(delta=SimpleNamespace(content=text))] if text is not None else []
    return _Chunk(choices=choices, usage=usage)


class TestStreamSummary:
    def test_streams_text_and_reports_usage(self, monkeypatch):
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        calls = {}

        class Completions:
            def create(self, **kwargs):
                calls.update(kwargs)
                return iter([_chunk("Head"), _chunk(None), _chunk("line"), _chunk(usage=usage)])

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        monkeypatch.setattr(doc_summary, "make_client", lambda cfg: client)
        seen = []
        cfg = {"analysis": {"openai_model_cheap": "gpt-4o-mini"}}

        out = "".join(stream_summary(cfg, "8-K", "source", seen.append))

        assert out == "Headline"
        assert seen == [usage]
        assert calls["model"] == "gpt-4o-mini"
        assert calls["max_tokens"] == 1500
        assert calls["temperature"] == pytest.approx(0.2)
        assert calls["stream_options"] == {"include_usage": True}

    def test_empty_choices_skipped(self, monkeypatch):
        # Adversarial: chunks with no choices (usage-only) must not raise
        class Completions:
            def create(self, **kwargs):
                return iter([_chunk(usage=None), _chunk("ok")])

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        monkeypatch.setattr(doc_summary, "make_client", lambda cfg: client)
        assert "".join(stream_summary({}, "10-K", "s", lambda u: None)) == "ok"
