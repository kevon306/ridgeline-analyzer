"""Public home page ("/")."""
import streamlit as st

from accounts import current_user

st.title("Ridgeline Property Analytics")
st.caption("Real Estate Investment Analytics · Gwinnett County, Georgia")
st.write("Upload a list of candidate fix-and-flip properties. The screener estimates each one's resale value from recent "
         "nearby sales, screens out weak deals, and ranks the rest by projected return on investment. "
         "Every figure is a screening estimate, not a guarantee.")
if current_user():
    st.page_link("views/screener.py", label="Open the screener", icon=":material/search:")
else:
    st.page_link("views/login.py", label="Log in or create an account to use the screener", icon=":material/login:")
