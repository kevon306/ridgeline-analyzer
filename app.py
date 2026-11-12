"""Ridgeline Property Analytics: the router. Run with `streamlit run app.py` (the Procfile does this on the server).

Pages: "/" public home, "/login" log in or register, "/screener" the screening tool, "/dashboard" placeholder.
Private pages call accounts.require_login() first, which sends a logged-out visitor to /login.
"""
import streamlit as st

from accounts import current_user, logout

st.set_page_config(page_title="Ridgeline Property Analytics", page_icon="🏠", layout="wide")

pages = [
    st.Page("views/home.py", title="Home", icon=":material/home:", default=True),        # "/"
    st.Page("views/login.py", title="Log in", icon=":material/login:", url_path="login"),
    st.Page("views/screener.py", title="Screener", icon=":material/search:", url_path="screener"),
    st.Page("views/dashboard.py", title="Dashboard", icon=":material/dashboard:", url_path="dashboard"),
]
page = st.navigation(pages)

user = current_user()
if user:
    with st.sidebar:
        st.caption(f"Logged in as {user.get('display_name') or user['email']}")
        if st.button("Log out", key="logout"):
            logout()

page.run()
