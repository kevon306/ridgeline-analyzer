"""Accounts and the MySQL persistence layer. Every database here is in-memory SQLite ("sqlite://"); none is a file."""
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import accounts  # noqa: E402
from accounts import LOGIN_FAILED, RegistrationError, authenticate, register  # noqa: E402
from db import database_url, make_engine, run_rows, runs, users  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture
def engine():
    e = make_engine("sqlite://")
    assert e.url.database is None                     # in memory, nothing written to disk
    return e


def email():
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


# ---------------------------------------------------------------- persistence
def test_database_url_is_rewritten_for_pymysql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@db.host:3306/ridgeline")
    assert database_url() == "mysql+pymysql://u:p@db.host:3306/ridgeline"
    monkeypatch.delenv("DATABASE_URL")
    assert database_url() is None
    assert database_url("sqlite://") == "sqlite://"


def test_tables_are_created_with_the_mysql_types_asked_for(engine):
    names = set(engine.dialect.get_table_names(engine.connect()))
    assert {"users", "runs", "run_rows"} <= names
    ddl = {t.name: str(CreateTable(t).compile(dialect=mysql.dialect())) for t in (users, runs, run_rows)}
    assert "AUTO_INCREMENT" in ddl["users"] and "email VARCHAR(255) NOT NULL" in ddl["users"] and "UNIQUE (email)" in ddl["users"]
    assert "is_demo TINYINT(1) NOT NULL DEFAULT 0" in ddl["users"] and "created_at DATETIME DEFAULT CURRENT_TIMESTAMP" in ddl["users"]
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE" in ddl["runs"] and "as_of DATE" in ddl["runs"]
    assert "asking_price DECIMAL(12, 2)" in ddl["run_rows"] and "roi_pct DECIMAL(8, 3)" in ddl["run_rows"]
    assert "comps_json LONGTEXT" in ddl["run_rows"] and "FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE" in ddl["run_rows"]


def test_creating_tables_twice_keeps_the_data(engine):
    register(engine, "keep@example.com", PASSWORD)
    from db import metadata
    metadata.create_all(engine)
    assert authenticate(engine, "keep@example.com", PASSWORD)


def test_deleting_a_user_deletes_their_runs_and_rows(engine):
    u = register(engine, email(), PASSWORD)
    with engine.begin() as con:
        rid = con.execute(insert(runs).values(user_id=u["id"], name="TEST run", n_rows=1)).inserted_primary_key[0]
        con.execute(insert(run_rows).values(run_id=rid, row_id=1, address="1 TEST RD", status="ranked"))
        con.execute(delete(users).where(users.c.id == u["id"]))
        assert con.execute(select(runs)).all() == [] and con.execute(select(run_rows)).all() == []


# ---------------------------------------------------------------- accounts (functions)
def test_correct_login(engine):
    e = email()
    created = register(engine, e, PASSWORD, "Kevon")
    assert authenticate(engine, e, PASSWORD) == created == {"id": created["id"], "email": e, "display_name": "Kevon"}
    assert authenticate(engine, "  " + e.upper() + " ", PASSWORD)["id"] == created["id"]     # case and spaces ignored


def test_wrong_password_and_unknown_email_both_fail(engine):
    e = email()
    register(engine, e, PASSWORD)
    assert authenticate(engine, e, PASSWORD + "x") is None
    assert authenticate(engine, email(), PASSWORD) is None
    assert authenticate(engine, e, "") is None and authenticate(engine, e, "x" * 100) is None


def test_duplicate_registration_is_refused(engine):
    e = email()
    register(engine, e, PASSWORD)
    with pytest.raises(RegistrationError, match="already exists"):
        register(engine, e.upper(), "another-long-password")
    with engine.connect() as con:
        assert len(con.execute(select(users).where(users.c.email == e)).all()) == 1


@pytest.mark.parametrize("password, message", [("short-pass", None), ("nine-char", "at least 10"), ("é" * 37, "at most 72 bytes")])
def test_password_length_rules(engine, password, message):
    if message:
        with pytest.raises(RegistrationError, match=message):
            register(engine, email(), password)
    else:
        assert register(engine, email(), password)                                   # exactly 10 characters is enough


def test_bad_email_is_refused(engine):
    with pytest.raises(RegistrationError, match="valid email"):
        register(engine, "not-an-email", PASSWORD)


def test_only_a_bcrypt_hash_is_stored(engine):
    e = email()
    register(engine, e, PASSWORD)
    with engine.connect() as con:
        row = con.execute(select(users).where(users.c.email == e)).one()
    assert row.password_hash.startswith("$2") and len(row.password_hash) == 60
    assert PASSWORD not in "".join(str(v) for v in row)


# ---------------------------------------------------------------- the app (pages, redirects, session)
@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    accounts._engine_for.clear()                       # a fresh in-memory database for each test
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    return at


def go_to_login(at):
    at.switch_page("views/login.py")
    at.run()
    assert not at.exception
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def ui_register(at, e, password, confirm=None):
    go_to_login(at)
    at.text_input(key="register_email").input(e)
    at.text_input(key="register_password").input(password)
    at.text_input(key="register_confirm").input(password if confirm is None else confirm)
    button(at, "Create account").click()
    at.run()
    return at


def ui_login(at, e, password):
    go_to_login(at)
    at.text_input(key="login_email").input(e)
    at.text_input(key="login_password").input(password)
    button(at, "Log in").click()
    at.run()
    return at


def on_screener(at):
    return any("Upload your candidate list" in i.value for i in at.info)


def test_app_correct_login_reaches_the_screener(app):
    e = email()
    ui_register(app, e, PASSWORD)
    assert app.session_state["user"]["email"] == e and on_screener(app)                # registering logs you in
    app.session_state["user"] = None                                                   # forget the session
    ui_login(app, e, PASSWORD)
    assert not app.exception and app.session_state["user"]["email"] == e and on_screener(app)
    assert all(PASSWORD not in str(app.session_state[k]) for k in ("user",))


def test_app_wrong_password_and_unknown_email_show_the_same_message(app):
    e = email()
    ui_register(app, e, PASSWORD)
    app.session_state["user"] = None
    ui_login(app, e, "wrong-password-123")
    wrong = [x.value for x in app.error]
    ui_login(app, email(), PASSWORD)
    unknown = [x.value for x in app.error]
    assert wrong == unknown == [LOGIN_FAILED] == ["Incorrect email or password"]
    assert not app.session_state["user"] and not on_screener(app)


def test_app_duplicate_registration_shows_an_error(app):
    e = email()
    ui_register(app, e, PASSWORD)
    app.session_state["user"] = None
    ui_register(app, e, "another-long-password")
    assert [x.value for x in app.error] == ["An account with this email already exists."] and not app.session_state["user"]


def test_app_short_password_and_mismatch_are_refused(app):
    ui_register(app, email(), "too-short")
    assert [x.value for x in app.error] == ["Password must be at least 10 characters."]
    ui_register(app, email(), PASSWORD, confirm=PASSWORD + "!")
    assert [x.value for x in app.error] == ["The two passwords do not match."]


@pytest.mark.parametrize("page", ["views/screener.py", "views/dashboard.py"])
def test_app_private_pages_send_a_logged_out_visitor_to_login(app, page):
    app.switch_page(page)
    app.run()
    assert not app.exception
    assert [t.value for t in app.title] == ["Log in"] and [t.label for t in app.tabs] == ["Log in", "Create an account"]
    assert not on_screener(app)


def test_app_home_is_public(app):
    assert not app.exception and [t.value for t in app.title] == ["Ridgeline Property Analytics"]


def test_app_logout_clears_the_session(app):
    e = email()
    ui_register(app, e, PASSWORD)
    app.session_state["marker"] = "anything else in the session"
    app.run()
    button(app, "Log out").click()
    app.run()
    assert not app.exception
    assert "user" not in app.session_state and "marker" not in app.session_state
    assert [t.value for t in app.title] == ["Ridgeline Property Analytics"]                  # back on the public home
    app.switch_page("views/screener.py")
    app.run()
    assert [t.value for t in app.title] == ["Log in"]                                         # and locked out again
