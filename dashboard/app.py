#!/usr/bin/env python3
"""
Meridian Capital Partners — JARVIS Dashboard
Run: streamlit run dashboard/app.py --server.port 8502
"""

import os
import sys
import threading
import time
from pathlib import Path

import streamlit as st
import yaml

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401 — register tables
import risk.db  # noqa: F401
from dashboard.theme import inject_css  # noqa: E402
from data.db import get_engine, initialise_schema  # noqa: E402

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="JARVIS | Meridian Capital Partners",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_css()


# ---------------------------------------------------------------------------
# Engine (cached across reruns)
# ---------------------------------------------------------------------------
@st.cache_resource
def _engine():
    with open(_ROOT / "config.yaml") as fh:
        cfg = yaml.safe_load(fh)
    url = os.environ.get("DATABASE_URL", cfg["database"]["url"])
    eng = get_engine(url)
    initialise_schema(eng)
    return eng


@st.cache_data(ttl=3600)
def _cfg():
    with open(_ROOT / "config.yaml") as fh:
        return yaml.safe_load(fh)


engine = _engine()
cfg = _cfg()


# ---------------------------------------------------------------------------
# Auto-refresh during market hours (9:30–16:00 ET, weekdays)
# ---------------------------------------------------------------------------
def _refresh_loop():
    from datetime import datetime as _dt

    import pytz

    et = pytz.timezone("America/New_York")
    while True:
        time.sleep(300)
        now = _dt.now(et)
        if now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) <= (16, 0):
            st.rerun()


if "refresh_thread" not in st.session_state:
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()
    st.session_state["refresh_thread"] = True

# ---------------------------------------------------------------------------
# Nav pill bar
# ---------------------------------------------------------------------------
PAGES = {
    "I": "PORTFOLIO",
    "II": "RESEARCH",
    "III": "RISK",
    "IV": "PERFORMANCE",
    "V": "EXECUTION",
    "VI": "LETTER",
    "VII": "TICKER",
}

if "page" not in st.session_state:
    st.session_state["page"] = "I"


def _nav():
    cols = st.columns(len(PAGES))
    for col, (num, label) in zip(cols, PAGES.items(), strict=False):
        active = st.session_state["page"] == num
        _style = "pill pill-active" if active else "pill"
        if col.button(
            f"{num} {label}",
            key=f"nav_{num}",
            use_container_width=True,
        ):
            st.session_state["page"] = num
            st.rerun()


_nav()

# ---------------------------------------------------------------------------
# Route to page
# ---------------------------------------------------------------------------
page = st.session_state["page"]

if page == "I":
    from dashboard.page_portfolio import render
elif page == "II":
    from dashboard.page_research import render
elif page == "III":
    from dashboard.page_risk import render
elif page == "IV":
    from dashboard.page_performance import render
elif page == "V":
    from dashboard.page_execution import render
elif page == "VI":
    from dashboard.page_letter import render
elif page == "VII":
    from dashboard.page_ticker import render

render(engine, cfg)  # type: ignore[possibly-undefined]
