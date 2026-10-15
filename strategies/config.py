"""Runtime settings: environment variable first, then Streamlit secrets (hosted app), else default."""

from __future__ import annotations

import os


def get_setting(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:  # streamlit is optional (the GitHub Actions job doesn't import it)
        import streamlit as st

        value = st.secrets.get(name)  # raises if there is no secrets.toml
        if value:
            return str(value)
    except Exception:
        pass
    return default
