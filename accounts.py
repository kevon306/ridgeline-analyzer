"""Accounts: register, log in, log out, and the require_login() guard every private page calls first.

Passwords are hashed with bcrypt and never stored, logged or kept in session state; only the hash reaches the database.
A wrong password and an unknown email give the same message (LOGIN_FAILED), and an unknown email still costs one bcrypt
check, so the response does not reveal which emails have accounts.
"""
from __future__ import annotations

import re

import bcrypt
import streamlit as st
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from db import database_url, make_engine, users

LOGIN_FAILED = "Incorrect email or password"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_BYTES = 72              # bcrypt's limit; longer passwords are refused rather than silently cut
HOME_PAGE, LOGIN_PAGE = "views/home.py", "views/login.py"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())   # for unknown emails (equal timing)


class RegistrationError(ValueError):
    """Shown to the person registering (never contains the password)."""


def normalize_email(email) -> str:
    return str(email or "").strip().lower()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _matches(password: str, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed)
    except ValueError:                # e.g. longer than 72 bytes: cannot match anything
        return False


def register(engine: Engine, email: str, password: str, display_name: str | None = None) -> dict:
    """Create an account and return {id, email, display_name}. Raises RegistrationError with a message to show."""
    email = normalize_email(email)
    display_name = (display_name or "").strip() or None
    if not _EMAIL.match(email) or len(email) > 255:
        raise RegistrationError("Enter a valid email address.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise RegistrationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise RegistrationError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    if display_name and len(display_name) > 100:
        raise RegistrationError("Display name must be at most 100 characters.")
    try:
        with engine.begin() as con:
            if con.execute(select(users.c.id).where(users.c.email == email)).first():
                raise RegistrationError("An account with this email already exists.")
            new_id = con.execute(insert(users).values(email=email, password_hash=hash_password(password),
                                                      display_name=display_name)).inserted_primary_key[0]
    except IntegrityError:            # two registrations racing for the same email
        raise RegistrationError("An account with this email already exists.") from None
    return {"id": int(new_id), "email": email, "display_name": display_name}


def authenticate(engine: Engine, email: str, password: str) -> dict | None:
    """{id, email, display_name} when the email and password match an account, else None."""
    with engine.connect() as con:
        row = con.execute(select(users.c.id, users.c.email, users.c.display_name, users.c.password_hash)
                          .where(users.c.email == normalize_email(email))).first()
    if row is None:
        _matches(password or "", _DUMMY_HASH)
        return None
    if not _matches(password or "", row.password_hash.encode("ascii")):
        return None
    return {"id": int(row.id), "email": row.email, "display_name": row.display_name}


# ---------------------------------------------------------------- Streamlit side
@st.cache_resource(show_spinner=False)
def _engine_for(url: str) -> Engine:
    return make_engine(url)


def app_engine() -> Engine | None:
    """The shared engine for DATABASE_URL, or None when the server has no database configured."""
    url = database_url()
    return _engine_for(url) if url else None


def current_user() -> dict | None:
    return st.session_state.get("user")


def log_in(user: dict) -> None:
    st.session_state["user"] = {"id": user["id"], "email": user["email"], "display_name": user.get("display_name")}


def require_login() -> dict:
    """Call first on every private page: a logged-out visitor is sent to /login and the page stops."""
    user = current_user()
    if not user:
        st.switch_page(LOGIN_PAGE)
    return user


def logout() -> None:
    """Forget everything about this browser session, then go home."""
    st.session_state.clear()
    st.switch_page(HOME_PAGE)
