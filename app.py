"""
Stock Analysis - local dashboard home page.

Run with:  streamlit run app.py

Each strategy lives in its own file under pages/ (Streamlit auto-generates
the sidebar navigation from that folder). To add a new strategy later:
  1. Put its core logic in strategies/<name>.py as plain functions/dataclasses
     (no printing, no argparse) so it can be reused from a CLI too.
  2. Add pages/N_<Name>.py that calls it and renders buttons + tables/charts,
     following the same pattern as pages/1_NSE_ETF_Weekly_Momentum.py.
"""

import streamlit as st

st.set_page_config(page_title="Stock Analysis", page_icon="📈", layout="wide")

st.title("📈 Stock Analysis Dashboard")
st.write(
    "Local dashboard for running stock/ETF strategies and viewing results. "
    "Pick a strategy from the sidebar."
)

st.subheader("Available strategies")
st.markdown(
    "- **NSE ETF Weekly Momentum** - tracks weekly volume + price trend for all "
    "NSE-listed ETFs and flags short-term momentum candidates.\n"
    "- **Sector Reversal** - flags NSE sectors that were falling and just turned positive.\n"
    "- **Position Guide** - reads your open positions workbook and flags each lot/stock "
    "as Hold, Add More, or Exit based on your sizing and exit rules.\n"
    "\nMore strategies will show up here as they're added."
)

st.info("Select a strategy in the sidebar to get started.", icon="👈")
