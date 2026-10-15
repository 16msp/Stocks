"""Optional password gate for the hosted app. Off unless an APP_PASSWORD setting exists,
so local use is unchanged."""

import hmac

import streamlit as st

from strategies.config import get_setting


def require_password() -> None:
    expected = get_setting("APP_PASSWORD")
    if not expected or st.session_state.get("_authed"):
        return
    st.title("🔒 Stock Analysis")
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, expected):
        st.session_state["_authed"] = True
        st.rerun()
    if entered:
        st.error("Wrong password.")
    st.stop()
