"""Design tokens and CSS injection for the JARVIS dashboard."""

import streamlit as st

DARK_BG = "#0b0e17"
CARD_GRAD_A = "#131827"
CARD_GRAD_B = "#1a2035"
ACCENT = "#6366f1"
LONG_COL = "#10b981"
SHORT_COL = "#f43f5e"
NEUTRAL = "#94a3b8"
TEXT_PRIMARY = "#e2e8f0"
TEXT_MUTED = "#64748b"
FONT_SANS = "Plus Jakarta Sans"
FONT_MONO = "JetBrains Mono"

_GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;600;700;800"
    "&family=JetBrains+Mono:wght@400;500&display=swap"
)

_CSS = f"""
@import url('{_GOOGLE_FONTS}');

html, body, [class*="css"] {{
    font-family: '{FONT_SANS}', sans-serif;
    background-color: {DARK_BG};
    color: {TEXT_PRIMARY};
}}

/* Hide Streamlit chrome */
#MainMenu, header, footer, .stDeployButton {{display: none !important;}}
[data-testid="stToolbar"] {{display: none !important;}}
[data-testid="stDecoration"] {{display: none !important;}}
.stApp > header {{display: none !important;}}
section[data-testid="stSidebar"] {{display: none !important;}}

/* Global container */
.block-container {{
    padding: 1.5rem 2rem;
    max-width: 100%;
}}

/* Cards */
.card {{
    background: linear-gradient(135deg, {CARD_GRAD_A}, {CARD_GRAD_B});
    border: 1px solid rgba(99,102,241,0.15);
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 0.75rem;
}}

.metric-card {{
    background: linear-gradient(135deg, {CARD_GRAD_A}, {CARD_GRAD_B});
    border: 1px solid rgba(99,102,241,0.12);
    border-radius: 10px;
    padding: 0.9rem 1rem;
    text-align: center;
    min-height: 80px;
}}
.metric-card .label {{
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: {TEXT_MUTED};
    font-weight: 600;
    margin-bottom: 0.3rem;
}}
.metric-card .value {{
    font-size: 1.5rem;
    font-weight: 700;
    font-family: '{FONT_MONO}', monospace;
    line-height: 1;
}}

/* Nav pill bar */
.pill-nav {{
    display: flex;
    gap: 0.4rem;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
}}
.pill {{
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.4rem 0.9rem;
    border-radius: 999px;
    border: 1px solid rgba(99,102,241,0.3);
    background: transparent;
    color: {NEUTRAL};
    cursor: pointer;
    transition: all 0.15s;
}}
.pill:hover {{
    border-color: {ACCENT};
    color: {TEXT_PRIMARY};
}}
.pill-active {{
    background: linear-gradient(135deg, {ACCENT}, #818cf8);
    border-color: transparent;
    color: #fff;
}}

/* Badges */
.long-badge {{
    background: rgba(16,185,129,0.15);
    color: {LONG_COL};
    border: 1px solid rgba(16,185,129,0.3);
    border-radius: 6px;
    padding: 0.15rem 0.5rem;
    font-size: 0.7rem;
    font-weight: 700;
    font-family: '{FONT_MONO}', monospace;
}}
.short-badge {{
    background: rgba(244,63,94,0.15);
    color: {SHORT_COL};
    border: 1px solid rgba(244,63,94,0.3);
    border-radius: 6px;
    padding: 0.15rem 0.5rem;
    font-size: 0.7rem;
    font-weight: 700;
    font-family: '{FONT_MONO}', monospace;
}}
.veto-reason {{
    background: rgba(244,63,94,0.08);
    border-left: 3px solid {SHORT_COL};
    padding: 0.5rem 0.8rem;
    border-radius: 0 6px 6px 0;
    font-size: 0.8rem;
    color: {SHORT_COL};
    margin-top: 0.3rem;
}}
.vix-low  {{ color: {LONG_COL};  font-weight: 700; }}
.vix-mid  {{ color: #f59e0b;      font-weight: 700; }}
.vix-high {{ color: {SHORT_COL}; font-weight: 700; }}

/* Monospace data */
.mono {{
    font-family: '{FONT_MONO}', monospace;
    font-size: 0.85rem;
}}

/* Section header */
.section-header {{
    font-size: 0.65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: {ACCENT};
    border-bottom: 1px solid rgba(99,102,241,0.2);
    padding-bottom: 0.4rem;
    margin: 1.2rem 0 0.8rem;
}}

/* Override Streamlit dataframe background */
[data-testid="stDataFrame"] {{background: transparent;}}

/* Input / chat */
[data-testid="stChatInput"] textarea {{
    background: {CARD_GRAD_A};
    border: 1px solid rgba(99,102,241,0.3);
    color: {TEXT_PRIMARY};
    border-radius: 8px;
}}
[data-testid="stChatMessage"] {{
    background: {CARD_GRAD_A};
    border-radius: 8px;
    border: 1px solid rgba(99,102,241,0.1);
}}
"""


def inject_css() -> None:
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def card(content: str) -> None:
    st.markdown(f'<div class="card">{content}</div>', unsafe_allow_html=True)


def metric_card(label: str, value: str, colour: str = TEXT_PRIMARY) -> None:
    st.markdown(
        f'<div class="metric-card">'
        f'<div class="label">{label}</div>'
        f'<div class="value" style="color:{colour}">{value}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def section_header(title: str) -> None:
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)


def vix_badge(vix: float | None) -> str:
    if vix is None:
        return '<span class="vix-mid">—</span>'
    if vix < 15:
        return f'<span class="vix-low">LOW VIX ({vix:.1f})</span>'
    if vix < 25:
        return f'<span class="vix-mid">CAUTION ({vix:.1f})</span>'
    return f'<span class="vix-high">STRESS ({vix:.1f})</span>'
