"""Ridgeline Property Analytics: the router. Run with `streamlit run app.py` (the Procfile does this on the server).

Pages: "/" public home, "/login" log in or register, "/screener" the screening tool, "/saved" my saved runs,
"/dashboard" placeholder.
Private pages call accounts.require_login() first, which sends a logged-out visitor to /login.
"""
import streamlit as st

from accounts import app_engine, current_user, logout
from seed_demo import seed_from_environment

st.set_page_config(page_title="Ridgeline Property Analytics", page_icon="🏠", layout="wide")

pages = [
    st.Page("views/home.py", title="Home", icon=":material/home:", default=True),        # "/"
    st.Page("views/login.py", title="Log in", icon=":material/login:", url_path="login"),
    st.Page("views/screener.py", title="Screener", icon=":material/search:", url_path="screener"),
    st.Page("views/saved.py", title="My saved runs", icon=":material/history:", url_path="saved"),
    st.Page("views/dashboard.py", title="Dashboard", icon=":material/dashboard:", url_path="dashboard"),
]
page = st.navigation(pages)


@st.cache_resource(show_spinner=False)
def _demo_seeded_once() -> bool:
    """When DEMO_PASSWORD is set on the server, create the demo user and its synthetic runs once per process
    (safe to repeat: nothing that exists is created again)."""
    try:
        return seed_from_environment(app_engine()) is not None
    except Exception as e:                       # e.g. database unreachable: log it, never break the pages
        print(f"Demo seed skipped: {type(e).__name__}")
        return False


_demo_seeded_once()

user = current_user()
if user:
    with st.sidebar:
        st.caption(f"Logged in as {user.get('display_name') or user['email']}")
        if st.button("Log out", key="logout"):
            logout()

page.run()
