"""Dashboard placeholder ("/dashboard"). Logged-in users only."""
import streamlit as st

from accounts import require_login

user = require_login()                            # first: a logged-out visitor is sent to /login

st.title("Dashboard")
st.info("Coming soon: your saved screening runs will appear here.")
