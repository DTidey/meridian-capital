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
