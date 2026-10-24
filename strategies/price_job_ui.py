"""Sidebar block for the Streamlit pages: when the stored prices were last updated and a button that starts the GitHub price update."""

from __future__ import annotations

import time

import streamlit as st

from strategies import price_job

MIN_SECONDS_BETWEEN_CLICKS = 60  # a double click or a restless session must not start a second run


@st.cache_data(ttl=20, show_spinner=False)
def _runs() -> tuple:
    """(runs, error text): cached for 20 seconds so reruns of the page do not call GitHub every time."""
    try:
        return price_job.recent_runs(5), ""
    except price_job.JobError as exc:
        return [], str(exc)


def _run() -> None:
    last = st.session_state.get("pj_clicked", 0.0)
    if time.time() - last < MIN_SECONDS_BETWEEN_CLICKS:
        st.session_state["pj_flash"] = ("warning", "An update was just started from this page. Give it a minute.")
        return
    st.session_state["pj_clicked"] = time.time()
    try:
        st.session_state["pj_flash"] = ("success", price_job.run_now())
    except price_job.JobError as exc:
        st.session_state["pj_flash"] = ("error", str(exc))
    _runs.clear()


def sidebar_price_update(latest_session=None) -> None:
    """Show the latest stored session and, when a GitHub token is set, the last runs of the price job and a button that starts one."""
    sb = st.sidebar
    sb.markdown("**Prices**")
    if latest_session:
        sb.caption(f"Latest stored session: {latest_session}")
    if not price_job.configured():
        sb.caption("The prices update by themselves after the market closes (GitHub's schedule can be late). To start an update from here, add GITHUB_DISPATCH_TOKEN to the app's secrets: see DEPLOY.md.")
        return
    flash = st.session_state.pop("pj_flash", None)
    if flash:
        getattr(sb, flash[0])(flash[1])
    runs, error = _runs()
    if error:
        sb.caption(f"⚠️ {error}")
    elif runs:
        last = runs[0]
        sb.caption(f"Last price update: {price_job.describe(last)}, started {last['started']} ([open on GitHub]({last['url']}))")
    busy = bool(runs) and runs[0]["status"] in price_job.ACTIVE
    sb.button("▶ Run price update now", on_click=_run, width="stretch", disabled=busy,
              help="Starts the GitHub job that downloads the sessions that are still missing. It takes a few minutes; press Recalculate when it has finished.")
