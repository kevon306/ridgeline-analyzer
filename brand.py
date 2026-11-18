"""Ridgeline Property Analytics identity, shared by every page: the same navy-and-copper masthead, R mark and colours
as the firm's reports since Phase 3 (Requirements Brief, Midpoint Status Report, Project Closeout)."""
import html

import streamlit as st

NAVY, COPPER, COPPER_LIGHT, TINT, INK, MUTED = "#16233F", "#B0602F", "#E3C7B6", "#EEF1F6", "#16233F", "#5A6472"
HOME_HEADLINE = "Screen a whole list of flip candidates in minutes, not one listing at a time"
NAME, TAGLINE = "Ridgeline Property Analytics", "Real Estate Investment Analytics · Georgia"


def apply_brand() -> None:
    """Brand CSS for every page (masthead, R mark, panels, buttons). Called once per run from app.py."""
    st.markdown(f"""<style>
.rl-mast {{display: flex; align-items: center; gap: 0.9rem; padding-bottom: 0.8rem; border-bottom: 4px solid {NAVY};
           margin-bottom: 1.2rem;}}
.rl-mark {{width: 2.6rem; height: 2.6rem; background: {NAVY}; border: 3px solid {COPPER}; color: {COPPER_LIGHT};
           display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1.25rem;
           flex: 0 0 auto;}}
.rl-name {{color: {NAVY}; font-weight: 800; font-size: 1.45rem; letter-spacing: 0.04em; line-height: 1.1;
           text-transform: uppercase;}}
.rl-tag {{color: {COPPER}; font-weight: 700; font-size: 0.72rem; letter-spacing: 0.28em; text-transform: uppercase;
          margin-top: 0.2rem;}}
.rl-outcome {{border-left: 6px solid {COPPER}; background: {TINT}; padding: 0.9rem 1.1rem; color: {INK};
              font-size: 1.08rem; font-weight: 700; line-height: 1.45; margin: 0.4rem 0 1rem 0;}}
.rl-tiles {{display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.9rem; margin: 0.6rem 0 1.2rem 0;}}
.rl-tile {{background: {TINT}; border-top: 4px solid {COPPER}; padding: 0.8rem 1rem;}}
.rl-tile b {{display: block; color: {NAVY}; font-size: 1.02rem; margin-bottom: 0.25rem;}}
.rl-tile span {{color: {MUTED}; font-size: 0.92rem; line-height: 1.45;}}
.rl-kicker {{color: {COPPER}; font-weight: 700; font-size: 0.75rem; letter-spacing: 0.22em; text-transform: uppercase;
             margin: 1.2rem 0 0.3rem 0;}}
.rl-q {{color: {NAVY}; font-size: 1.25rem; font-weight: 700; margin: 0 0 0.6rem 0; line-height: 1.35;}}
h1, h2, h3 {{color: {NAVY};}}
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a {{background: {NAVY}; border: 2px solid {COPPER}; border-radius: 4px;
    padding: 0.35rem 0.9rem; justify-content: center;}}
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a * {{color: #fff !important; font-weight: 600;}}
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a:hover {{background: {COPPER};}}
@media (max-width: 640px) {{ .rl-name {{font-size: 1.1rem;}} .rl-tag {{letter-spacing: 0.15em;}} }}
</style>""", unsafe_allow_html=True)


def masthead() -> None:
    """The R mark, firm name and tagline, as on the reports."""
    st.markdown(f"<div class='rl-mast'><div class='rl-mark'>R</div><div><div class='rl-name'>{html.escape(NAME)}</div>"
                f"<div class='rl-tag'>{html.escape(TAGLINE)}</div></div></div>", unsafe_allow_html=True)
