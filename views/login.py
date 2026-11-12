"""Log in and register ("/login")."""
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from accounts import (LOGIN_FAILED, MIN_PASSWORD_LENGTH, RegistrationError, app_engine, authenticate, current_user,
                      log_in, register)

st.title("Log in")
user = current_user()
if user:
    st.success(f"You are logged in as {user['email']}.")
    st.page_link("views/screener.py", label="Open the screener", icon=":material/search:")
    st.stop()

engine = app_engine()
if engine is None:
    st.error("Accounts are unavailable: this server has no DATABASE_URL configured.")
    st.stop()

login_tab, register_tab = st.tabs(["Log in", "Create an account"])
with login_tab:
    with st.form("login_form", clear_on_submit=True):       # clear_on_submit: the password does not linger in the form
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        try:
            found = authenticate(engine, email, password)
        except SQLAlchemyError:
            st.error("The accounts database could not be reached. Please try again shortly.")
            st.stop()
        if found:
            log_in(found)
            st.switch_page("views/screener.py")
        st.error(LOGIN_FAILED)

with register_tab:
    with st.form("register_form", clear_on_submit=True):
        new_email = st.text_input("Email", key="register_email")
        name = st.text_input("Display name (optional)", key="register_name")
        new_password = st.text_input(f"Password (at least {MIN_PASSWORD_LENGTH} characters)", type="password", key="register_password")
        confirm = st.text_input("Confirm password", type="password", key="register_confirm")
        created = st.form_submit_button("Create account")
    if created:
        if new_password != confirm:
            st.error("The two passwords do not match.")
        else:
            try:
                log_in(register(engine, new_email, new_password, name))
                st.switch_page("views/screener.py")
            except RegistrationError as e:
                st.error(str(e))
            except SQLAlchemyError:
                st.error("The accounts database could not be reached. Please try again shortly.")
