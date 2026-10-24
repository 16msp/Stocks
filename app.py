"""
Stock Analysis - dashboard home page.

Run with:  streamlit run app.py

Each strategy lives in its own file under pages/ (Streamlit auto-generates
the sidebar navigation from that folder). To add a new strategy later:
  1. Put its core logic in strategies/<name>.py as plain functions/dataclasses
     (no printing, no argparse) so it can be reused from a CLI too.
  2. Add pages/N_<Name>.py that calls it and renders buttons + tables/charts,
     following the same pattern as pages/1_NSE_ETF_Weekly_Momentum.py.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent))

st.set_page_config(page_title="Stock Analysis", page_icon="📈", layout="wide")

from strategies.auth_gate import require_password  # noqa: E402

require_password()

from strategies import price_job_ui  # noqa: E402

price_job_ui.sidebar_price_update()

st.title("📈 Stock Analysis Dashboard")
st.write("Technical analysis for NSE stocks. Pick a page from the sidebar.")

st.subheader("Pages")
st.markdown(
    "- **Position Guide** - reads your open positions sheet and, for each stock you own, shows relative "
    "strength vs Nifty, stabilisation, accumulation, volume-profile and supply indicators with a Buy Score. "
    "Also lists tranches to exit and stocks you could add to, ranked by timing.\n"
    "- **Sector Scanner** - pulls every NSE-listed stock in a sector (32 sectors: banks, finance, IT, pharma, FMCG, auto, metals and more), runs the same "
    "checks and gives each a recommendation: Accumulate, Strong - wait for dip, Watch, or Avoid. "
    "Pick a sector from the sidebar dropdown.\n"
    "- **Mean Reversion** - an RSI(2) dip-buying strategy: lists stocks in a long-term uptrend that have just fallen several sessions in a row, with the exit rule, "
    "paper trading (orders fill at the next open) and the backtest results.\n"
    "- **Dip Watchlist** - a hand-picked list tracked by its fall from the all-time high with a turn signal (has it stopped falling?), and a scan of every stock for deep falls that have turned.\n"
    "\nTechnicals only - check fundamentals before buying. Not investment advice."
)

st.info("Select a page in the sidebar to get started.", icon="👈")
