"""
Look and feel shared by every screen: colours, the logo, and small HTML pieces.

One palette across the demo and the deck: ink for text and structure, teal for
TrustLadder itself, and four fixed verdict colours so a verdict looks the same
on every screen and every slide.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

INK = "#1B2A41"
TEAL = "#0F766E"
MUTED = "#64748B"
VERDICT_COLOUR = {"Authentic": "#15803D", "Suspicious": "#B45309",
                  "Tampered": "#B91C1C", "Inconclusive": "#475569"}
REGISTRY_COLOUR = {"Verified": "#15803D", "Mismatch": "#B91C1C", "No record": "#B45309",
                   "Not covered": "#475569", "Not asked": "#475569"}
STATUS_COLOUR = {"Paid": "#15803D", "Paid after review": "#15803D", "In review": "#B45309",
                 "On hold": "#B91C1C", "Waiting for customer": "#475569",
                 "Waiting for hospital": "#475569", "Rejected": "#7F1D1D"}

_LOGO = (Path(__file__).resolve().parent.parent / "assets" / "logo.svg").read_text()
LOGO_URI = "data:image/svg+xml;base64," + base64.b64encode(_LOGO.encode()).decode()


def logo_html(size: int = 34, wordmark: bool = True, dark: bool = False) -> str:
    colour = "white" if dark else INK
    word = (f'<span style="font-weight:800;font-size:{size * 0.62:.0f}px;color:{colour};'
            f'letter-spacing:-0.3px">Trust<span style="color:{TEAL}">Ladder</span></span>') if wordmark else ""
    return (f'<div style="display:flex;align-items:center;gap:10px">'
            f'<img src="{LOGO_URI}" width="{size}" height="{size}" alt="TrustLadder logo"/>{word}</div>')


def chip(text: str, colour: str) -> str:
    return f'<span class="tl-chip" style="background:{colour}">{text}</span>'


def inr(paise: int) -> str:
    from trustladder.bills import format_inr
    return "₹" + format_inr(paise)[:-3]


def apply_css() -> None:
    st.markdown(f"""
<style>
  #MainMenu, footer, [data-testid="stToolbar"] {{ visibility: hidden; }}
  .block-container {{ padding-top: 1.4rem; max-width: 1240px; }}
  h1, h2, h3 {{ color: {INK}; }}
  [data-testid="stSidebar"] {{ background: #F8FAFC; border-right: 1px solid #E2E8F0; }}
  .tl-chip {{ display:inline-block; padding:2px 10px; border-radius:999px; color:white;
             font-weight:600; font-size:0.82rem; margin-right:4px; }}
  .tl-verdict {{ border-radius:10px; padding:14px 18px; color:white; margin:4px 0 10px 0; }}
  .tl-verdict .big {{ font-size:1.6rem; font-weight:700; }}
  .tl-verdict .sub {{ font-size:0.92rem; opacity:0.95; }}
  .tl-stage-title {{ font-weight:700; color:{INK}; font-size:1.0rem; }}
  .tl-ai {{ color:{TEAL}; font-size:0.78rem; font-style:italic; margin:2px 0 6px 0; }}
  .tl-muted {{ color:{MUTED}; font-size:0.86rem; }}
  .tl-page-title {{ font-size:1.55rem; font-weight:750; color:{INK}; margin:0 0 2px 0; }}
  .tl-page-sub {{ color:{MUTED}; font-size:0.95rem; margin-bottom:14px; }}
  .tl-kpi {{ background:#F8FAFC; border:1px solid #E2E8F0; border-radius:10px; padding:12px 14px; }}
  .tl-kpi .v {{ font-size:1.6rem; font-weight:750; color:{INK}; line-height:1.1; }}
  .tl-kpi .l {{ font-size:0.82rem; color:{MUTED}; margin-top:2px; }}
  .tl-card {{ border:1px solid #E2E8F0; border-radius:10px; padding:12px 16px; margin-bottom:10px; background:white; }}
  .tl-role {{ display:inline-block; background:{TEAL}; color:white; border-radius:6px; padding:1px 8px;
             font-size:0.75rem; font-weight:700; }}
  .tl-layer {{ display:flex; gap:8px; align-items:stretch; background:#F1F5F9; border-radius:8px;
              padding:8px; margin-bottom:6px; }}
  .tl-layer.found {{ background:{INK}; }}
  .tl-lname {{ width:180px; flex:none; font-weight:700; color:{INK}; font-size:0.9rem; }}
  .tl-layer.found .tl-lname {{ color:white; }}
  .tl-lname span {{ display:block; font-weight:400; font-style:italic; color:{MUTED}; font-size:0.78rem; }}
  .tl-comp {{ flex:1; border-radius:7px; padding:8px 6px; text-align:center; font-weight:700;
             font-size:0.78rem; display:flex; align-items:center; justify-content:center; }}
  .tl-comp.built {{ background:{TEAL}; color:white; border:1.5px solid {TEAL}; }}
  .tl-comp.target {{ background:white; color:{TEAL}; border:1.5px dashed {TEAL}; }}
</style>""", unsafe_allow_html=True)


def page_title(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="tl-page-title">{title}</div>'
                + (f'<div class="tl-page-sub">{subtitle}</div>' if subtitle else ""),
                unsafe_allow_html=True)


def kpis(items: list[tuple[str, str]]) -> None:
    """A row of number tiles: [(value, label), ...]."""
    cols = st.columns(len(items))
    for col, (value, label) in zip(cols, items):
        col.markdown(f'<div class="tl-kpi"><div class="v">{value}</div><div class="l">{label}</div></div>',
                     unsafe_allow_html=True)
