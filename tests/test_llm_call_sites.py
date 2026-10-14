"""Wiring tests: every LLM call site uses the shared client factory and configured model."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

_CFG = {"llm": {"provider": "openrouter"}, "analysis": {"openai_model": "gpt-4o-mini"}}


def _completion(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _chunk(text: str | None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class TestReportingCallSites:
    def _run(self, module):
        fake = MagicMock()
        fake.chat.completions.create.return_value = _completion("  body text  ")
        with patch("analysis.llm.make_client", return_value=fake) as mock_make:
            out = module._call_llm("{}", _CFG)
        mock_make.assert_called_once_with(_CFG)
        assert fake.chat.completions.create.call_args.kwargs["model"] == "openai/gpt-4o-mini"
        assert out == "body text"

    def test_lp_letter_uses_factory_and_config_model(self):
        from reporting import lp_letter

        self._run(lp_letter)

    def test_commentary_uses_factory_and_config_model(self):
        from reporting import commentary

        self._run(commentary)


class TestJarvisStream:
    def _stream(self, chunks):
        from dashboard import jarvis

        fake = MagicMock()
        fake.chat.completions.create.return_value = iter(chunks)
        with patch("analysis.llm.make_client", return_value=fake) as mock_make:
            out = list(jarvis._stream_reply(_CFG, [{"role": "user", "content": "hi"}]))
        return out, fake, mock_make

    def test_uses_factory_and_config_model(self):
        _, fake, mock_make = self._stream([_chunk("ok")])
        mock_make.assert_called_once_with(_CFG)
        kwargs = fake.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-4o-mini"
        assert kwargs["stream"] is True

    def test_empty_choices_chunk_is_skipped(self):
        usage_only = SimpleNamespace(choices=[], usage=SimpleNamespace(cost=0.001))
        out, _, _ = self._stream([_chunk("Hel"), _chunk("lo"), usage_only])
        assert "".join(out) == "Hello"

    def test_none_delta_content_is_skipped(self):
        out, _, _ = self._stream([_chunk(None), _chunk("x"), _chunk(None)])
        assert "".join(out) == "x"


_CFG_REASONING = {"llm": {"provider": "openrouter"}, "analysis": {"openai_model": "gpt-6-sol"}}


class TestReasoningParams:
    def _reporting_kwargs(self, module, cfg):
        fake = MagicMock()
        fake.chat.completions.create.return_value = _completion("body")
        with patch("analysis.llm.make_client", return_value=fake):
            module._call_llm("{}", cfg)
        return fake.chat.completions.create.call_args.kwargs

    def test_letter_and_commentary_classic_unchanged(self):
        from reporting import commentary, lp_letter

        letter = self._reporting_kwargs(lp_letter, _CFG)
        assert (letter["max_tokens"], letter["temperature"]) == (600, 0.7)
        comm = self._reporting_kwargs(commentary, _CFG)
        assert (comm["max_tokens"], comm["temperature"]) == (800, 0.7)

    def test_letter_and_commentary_reasoning(self):
        from reporting import commentary, lp_letter

        for module, limit in ((lp_letter, 600), (commentary, 800)):
            kw = self._reporting_kwargs(module, _CFG_REASONING)
            assert kw["model"] == "openai/gpt-6-sol"
            assert "temperature" not in kw and "max_tokens" not in kw
            assert kw["max_completion_tokens"] == limit + 4000
            assert kw["reasoning_effort"] == "low"

    def test_jarvis_reasoning(self):
        from dashboard import jarvis

        fake = MagicMock()
        fake.chat.completions.create.return_value = iter([_chunk("ok")])
        with patch("analysis.llm.make_client", return_value=fake):
            list(jarvis._stream_reply(_CFG_REASONING, [{"role": "user", "content": "hi"}]))
        kw = fake.chat.completions.create.call_args.kwargs
        assert "temperature" not in kw
        assert kw["max_completion_tokens"] == 600 + 4000

    def test_jarvis_classic_unchanged(self):
        from dashboard import jarvis

        fake = MagicMock()
        fake.chat.completions.create.return_value = iter([_chunk("ok")])
        with patch("analysis.llm.make_client", return_value=fake):
            list(jarvis._stream_reply(_CFG, [{"role": "user", "content": "hi"}]))
        kw = fake.chat.completions.create.call_args.kwargs
        assert (kw["max_tokens"], kw["temperature"]) == (600, 0.7)

    def test_summary_reasoning(self):
        from analysis import doc_summary

        fake = MagicMock()
        fake.chat.completions.create.return_value = iter([])
        cfg = {"llm": {"provider": "openrouter"}, "analysis": {"openai_model_cheap": "gpt-6-luna"}}
        with patch.object(doc_summary, "make_client", return_value=fake):
            list(doc_summary.stream_summary(cfg, "8-K", "text", lambda u: None))
        kw = fake.chat.completions.create.call_args.kwargs
        assert kw["model"] == "openai/gpt-6-luna"
        assert "temperature" not in kw
        assert kw["max_completion_tokens"] == 1500 + 4000
        assert kw["stream_options"] == {"include_usage": True}
