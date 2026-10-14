"""Helpers shared by the Ticker page and its section modules."""

from __future__ import annotations

from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL


def score_colour(val: float | None) -> str:
    if val is None:
        return NEUTRAL
    if val >= 60:
        return LONG_COL
    if val <= 40:
        return SHORT_COL
    return NEUTRAL


def chart_layout(height: int = 300, left_margin: int = 50) -> dict:
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font_color": "#e2e8f0",
        "height": height,
        "margin": {"t": 10, "b": 30, "l": left_margin, "r": 60},
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "rgba(99,102,241,0.2)",
            "borderwidth": 1,
        },
        "xaxis": {"gridcolor": "rgba(255,255,255,0.05)"},
        "yaxis": {"gridcolor": "rgba(255,255,255,0.05)"},
    }
