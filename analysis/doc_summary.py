"""AI summaries of SEC filings for the dashboard document viewer."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from analysis.llm import DEFAULT_MODEL_CHEAP, completion_params, make_client, resolve_model

MAX_SOURCE_TOKENS = 100_000  # set for gpt-4o-mini (128k context); gpt-6-luna allows 1.05M
MAX_OUTPUT_TOKENS = 1_500

_RULES = (
    "Use markdown headings and short bullet points. Quote every figure exactly as it appears "
    "in the source; do not calculate, round or convert figures. Do not include links. If "
    "something is not in the source, leave it out; never invent figures or facts."
)
_PROMPTS = {
    "8-K": (
        "You summarise SEC 8-K filings and earnings releases for an equity analyst. Cover: "
        "headline results, guidance, notable items, and capital returns (dividends, "
        "buybacks). Skip sections the filing doesn't address. " + _RULES
    ),
    "periodic": (
        "You summarise SEC 10-K and 10-Q filings for an equity analyst. Cover: overview of the "
        "period, results, segments, liquidity and capital returns, risk factors (new or "
        "changed), and outlook. Skip sections the filing doesn't address. " + _RULES
    ),
}

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_REF_LINK = re.compile(r"!?\[([^\]]*)\]\[[^\]]*\]")
_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)
_AUTOLINK = re.compile(r"<(https?://[^>\s]*)>")


def summary_messages(form_type: str, text: str) -> list[dict]:
    prompt = _PROMPTS["8-K"] if form_type == "8-K" else _PROMPTS["periodic"]
    return [{"role": "system", "content": prompt}, {"role": "user", "content": text}]


def truncate_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Cut text to max_tokens (o200k_base, approximate for GPT-6); True if it was cut."""
    import tiktoken

    enc = tiktoken.get_encoding("o200k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text, False
    return enc.decode(tokens[:max_tokens]), True


def stream_summary(
    cfg: dict, form_type: str, text: str, on_usage: Callable[[object], None]
) -> Iterator[str]:
    """Yield the summary as text deltas; on_usage receives the final usage object."""
    client = make_client(cfg)
    model = summary_model(cfg)
    stream = client.chat.completions.create(
        model=model,
        messages=summary_messages(form_type, text),
        stream=True,
        stream_options={"include_usage": True},
        **completion_params(cfg, model, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2),
    )
    for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            on_usage(chunk.usage)
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def summary_model(cfg: dict) -> str:
    return resolve_model(
        cfg, (cfg.get("analysis") or {}).get("openai_model_cheap", DEFAULT_MODEL_CHEAP)
    )


def unverified_figures(summary: str, source: str) -> list[str]:
    """Figures in the summary (2+ digits, not years) not found as a whole number in source."""
    plain_source = source.replace(",", "")
    flagged: list[str] = []
    for figure in _NUMBER.findall(summary):
        figure = figure.rstrip(",")
        plain = figure.replace(",", "")
        digits = plain.replace(".", "")
        if len(digits) < 2 or re.fullmatch(r"(19|20)\d\d", plain) or figure in flagged:
            continue
        if not re.search(rf"(?<![\d.]){re.escape(plain)}(?!\d|\.\d)", plain_source):
            flagged.append(figure)
    return flagged


def clean_summary(text: str) -> str:
    """Links and images to plain text; $ escaped so Streamlit doesn't render LaTeX."""
    text = _REF_DEF.sub("", _REF_LINK.sub(r"\1", _LINK.sub(r"\1", text)))
    text = _AUTOLINK.sub(r"\1", text)
    return re.sub(r"(?<!\\)((?:\\\\)*)\$", r"\1\\$", text)
