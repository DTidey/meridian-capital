"""Page VI — Letter: formal daily LP letter with letterhead and compliance footer."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import streamlit as st

from reporting.db import lp_letters, portfolio_nav
from reporting.lp_letter import generate, render_full


def render(engine, cfg: dict) -> None:
    today = date.today().isoformat()
    rep_cfg = cfg.get("reporting", {})
    inc_date = rep_cfg.get("inception_date", "2024-01-02")

    with engine.connect() as conn:
        nav_row = conn.execute(
            sa.select(portfolio_nav.c.nav).order_by(portfolio_nav.c.date.desc()).limit(1)
        ).fetchone()
        nav_usd = float(nav_row[0]) if nav_row else 0.0

        cached = conn.execute(
            sa.select(lp_letters).where(lp_letters.c.letter_date == today)
        ).fetchone()

    # -----------------------------------------------------------------------
    # Generate if not cached
    # -----------------------------------------------------------------------
    if cached is None:
        with st.spinner("JARVIS composing today's letter…"):
            try:
                content = generate(engine, cfg=cfg, letter_date=today)
            except Exception as e:
                st.error(f"Letter generation failed: {e}")
                st.caption("Set `OPENAI_API_KEY` env var to enable JARVIS letter generation.")
                return
        with engine.connect() as conn:
            cached = conn.execute(
                sa.select(lp_letters).where(lp_letters.c.letter_date == today)
            ).fetchone()

    if cached is None:
        st.error("Could not generate or retrieve LP letter.")
        return

    letter_date = cached[0]
    content = cached[2]
    _doc_id = cached[1] or f"MCP-IM-{today[:4]}-{today[5:7]}{today[8:]}"

    # -----------------------------------------------------------------------
    # Render full letter
    # -----------------------------------------------------------------------
    full = render_full(letter_date, content, nav_usd=nav_usd, inception_date=inc_date)

    st.markdown(
        '<div style="max-width:800px;margin:0 auto;'
        "background:#f8f6f1;"
        "border:1px solid #d4c9b0;border-radius:8px;"
        "padding:2.5rem 3rem;font-family:Georgia,serif;line-height:1.8;"
        'color:#1a1a1a;">' + _md_to_html(full) + "</div>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------------
    # Regenerate button
    # -----------------------------------------------------------------------
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("↺ Regenerate Letter", type="secondary"):
        with st.spinner("JARVIS is rewriting…"):
            try:
                generate(engine, cfg=cfg, letter_date=today, force=True)
                st.rerun()
            except Exception as e:
                st.error(f"Regeneration failed: {e}")


def _md_to_html(md: str) -> str:
    """Very lightweight markdown → HTML for the letter card (no external dep)."""
    import re

    lines = md.split("\n")
    out = []
    for line in lines:
        # Headings
        if line.startswith("# "):
            out.append(
                f'<h1 style="font-size:1.4rem;letter-spacing:0.05em;color:#1a1a1a;">{line[2:]}</h1>'
            )
        elif line.startswith("## "):
            out.append(
                f'<h3 style="font-size:1rem;color:#5a5a5a;letter-spacing:0.08em;">{line[3:]}</h3>'
            )
        # Horizontal rule
        elif line.strip() == "---":
            out.append('<hr style="border-color:#d4c9b0;margin:0.8rem 0;" />')
        # Blockquote (CONFIDENTIAL stamp)
        elif line.startswith("> "):
            out.append(
                f'<div style="border:2px solid #8b1a1a;border-radius:4px;padding:0.3rem 0.8rem;'
                f'color:#8b1a1a;font-weight:700;font-size:0.85rem;letter-spacing:0.1em;margin:0.8rem 0;">'
                f"{line[2:]}</div>"
            )
        # Bold
        elif line.strip() == "":
            out.append("<br/>")
        else:
            # Inline bold/italic
            processed = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            processed = re.sub(r"\*(.+?)\*", r"<em>\1</em>", processed)
            out.append(f"<p style='margin:0.4rem 0;'>{processed}</p>")
    return "\n".join(out)
