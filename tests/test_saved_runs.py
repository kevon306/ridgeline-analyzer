"""Saved runs: saving, reopening, per-user isolation, the /saved page and the demo seed. Databases are in-memory
SQLite ("sqlite://"); the county data is read from the committed development extract."""
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import accounts  # noqa: E402
import results_view as rv  # noqa: E402
import seed_demo  # noqa: E402
from accounts import authenticate, register  # noqa: E402
from db import make_engine, run_rows, runs, users  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import load_candidates, load_parcel_index, score_batch  # noqa: E402
from runs import delete_run, list_runs, load_run, rebuild_scored, run_name, save_run  # noqa: E402

P = load_params(ROOT / "assumptions.json")
EXTRACT = ROOT / "data" / "gwinnett_extract.db"
FIXTURE = ROOT / "data" / "candidates_dev_fixture.csv"
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def scored():
    return score_batch(load_candidates(FIXTURE), EXTRACT, P)


@pytest.fixture
def engine():
    return make_engine("sqlite://")


def user(engine, tag="a"):
    return register(engine, f"{tag}-{uuid.uuid4().hex[:6]}@example.com", PASSWORD)["id"]


def display_top5(df):
    styler, _ = rv.results_table(df[df["status"] == "ranked"].head(5), "15%")
    return styler.data


# ---------------------------------------------------------------- saving and reopening
def test_saving_stores_every_row(engine, scored):
    uid = user(engine)
    rid = save_run(engine, uid, run_name("candidates.csv"), scored)
    run, rows = load_run(engine, uid, rid)
    assert run["n_rows"] == len(scored) == len(rows) == 20 and run["as_of"].isoformat() == P["as_of"]
    assert run["name"].startswith("candidates.csv · ")
    by_id = scored.set_index("row_id")
    for _, r in rows.iterrows():
        s = by_id.loc[r["row_id"]]
        assert (r["status"], r["reason"] or "") == (s["status"], s["reason"])
        assert r["address"] == s["address"] and r["zip"] == s["zip"]
        assert (pd.isna(r["comp_count"]) and pd.isna(s["comp_count"])) or r["comp_count"] == s["comp_count"]
        if isinstance(s["comps_json"], str):
            assert r["comps_json"] == s["comps_json"]                                   # stored verbatim
        else:
            assert pd.isna(r["comps_json"])
        for col, src, places in (("arv", "arv", 2), ("net_profit", "net_profit", 2), ("roi_pct", "roi_pct", 3),
                                 ("reno_likely_with_contingency", "reno_likely_with_contingency", 2)):
            assert (pd.isna(r[col]) and pd.isna(s[src])) or r[col] == pytest.approx(round(s[src], places))
        assert (pd.isna(r["rank_in_run"]) and pd.isna(s["rank"])) or r["rank_in_run"] == s["rank"]


def test_opening_a_saved_run_reproduces_the_same_top_5(engine, scored):
    uid = user(engine)
    rid = save_run(engine, uid, "TEST run", scored)
    run, rows = load_run(engine, uid, rid)
    rebuilt = rebuild_scored(run, rows, P, load_parcel_index(EXTRACT))
    pd.testing.assert_frame_equal(display_top5(scored).reset_index(drop=True), display_top5(rebuilt).reset_index(drop=True))
    ranked = rebuilt[rebuilt["status"] == "ranked"]
    assert ranked["reno_tier"].notna().all() and ranked["selling"].notna().all()         # breakdown reproduced exactly
    assert list(rebuilt["row_id"]) == list(scored["row_id"])                            # same order: ranked first, by rank


def test_breakdown_is_left_empty_when_today_s_assumptions_no_longer_reproduce_it(engine, scored):
    uid = user(engine)
    run, rows = load_run(engine, uid, save_run(engine, uid, "TEST run", scored))
    changed = {**P, "contingency": 0.25}                                                 # assumptions changed since saving
    rebuilt = rebuild_scored(run, rows, changed, load_parcel_index(EXTRACT))
    ranked = rebuilt[rebuilt["status"] == "ranked"]
    assert ranked["reno_tier"].isna().all() and ranked["selling"].isna().all()           # not recomputed with new numbers
    pd.testing.assert_frame_equal(display_top5(scored).reset_index(drop=True), display_top5(rebuilt).reset_index(drop=True))


# ---------------------------------------------------------------- isolation
def test_user_a_cannot_read_or_delete_user_b_s_run(engine, scored):
    a, b = user(engine, "a"), user(engine, "b")
    b_run = save_run(engine, b, "B's private run", scored)
    a_run = save_run(engine, a, "A's run", scored.head(3))
    assert list_runs(engine, a)["id"].tolist() == [a_run]
    assert load_run(engine, a, b_run) is None                                             # even with the right id
    assert delete_run(engine, a, b_run) is False and load_run(engine, b, b_run) is not None
    assert delete_run(engine, b, b_run) is True and load_run(engine, b, b_run) is None
    with engine.connect() as con:                                                         # rows went with the run
        assert con.execute(select(func.count()).select_from(run_rows).where(run_rows.c.run_id == b_run)).scalar() == 0


# ---------------------------------------------------------------- the pages
@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    accounts._engine_for.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    return at


def db_engine():
    return accounts._engine_for("sqlite://")                 # the app's own in-memory database


def log_in_as(at, email):
    at.switch_page("views/login.py"); at.run()
    at.text_input(key="register_email").input(email)
    at.text_input(key="register_password").input(PASSWORD)
    at.text_input(key="register_confirm").input(PASSWORD)
    next(b for b in at.button if b.label == "Create account").click()
    at.run()
    assert at.session_state["user"]["email"] == email


def score_example(at):
    at.switch_page("views/screener.py"); at.run()
    at.checkbox[0].check().run()
    assert not at.exception


def test_screener_saves_each_scored_list_once_for_the_logged_in_user(app):
    log_in_as(app, "saver@example.com")
    score_example(app)
    app.run(); app.run()                                                                  # reruns do not save again
    mine = list_runs(db_engine(), app.session_state["user"]["id"])
    assert len(mine) == 1 and mine["n_rows"].tolist() == [20] and mine["n_ranked"].tolist() == [14]
    assert mine["name"].iloc[0].startswith("Development example (synthetic asking prices) · ")
    assert any("Saved to your saved runs." == c.value for c in app.caption)


def test_nothing_is_saved_when_nobody_is_logged_in(app):
    app.switch_page("views/screener.py"); app.run()                                       # sent to /login, nothing scored
    assert [t.value for t in app.title] == ["Log in"]
    with db_engine().connect() as con:
        assert con.execute(select(func.count()).select_from(runs)).scalar() == 0


def test_saved_page_shows_the_same_top_5_as_the_screener(app):
    log_in_as(app, "viewer@example.com")
    score_example(app)
    screener_top5 = app.dataframe[0].value
    app.switch_page("views/saved.py"); app.run()
    assert not app.exception and [t.value for t in app.title] == ["My saved runs"]
    runs_table, saved_top5 = app.dataframe[0].value, app.dataframe[1].value
    assert runs_table["Rows"].tolist() == [20] and runs_table["Ranked"].tolist() == [14]
    pd.testing.assert_frame_equal(screener_top5.reset_index(drop=True), saved_top5.reset_index(drop=True))
    assert any("Expected budget" == m.label for m in app.metric)                         # full detail panel, as in the screener


def test_saved_page_never_opens_another_user_s_run(app):
    eng = db_engine()
    other = register(eng, "owner@example.com", PASSWORD)["id"]
    secret = save_run(eng, other, "Owner's private run", score_batch(load_candidates(FIXTURE), EXTRACT, P))
    log_in_as(app, "snoop@example.com")
    score_example(app)                                                                    # snoop has one run of their own
    app.switch_page("views/saved.py")
    app.session_state["saved_run"] = secret                                               # a guessed id
    app.run()
    assert not app.exception
    shown = [str(x) for d in app.dataframe for x in d.value.astype(str).values.ravel()]
    assert "Owner's private run" not in " ".join(shown) and "Owner's private run" not in str(app.selectbox[0].options)
    assert app.session_state["saved_run"] != secret


def test_logged_out_visitor_to_saved_is_sent_to_login(app):
    app.switch_page("views/saved.py"); app.run()
    assert not app.exception and [t.value for t in app.title] == ["Log in"]


def test_delete_needs_confirmation(app):
    log_in_as(app, "deleter@example.com")
    score_example(app)
    app.switch_page("views/saved.py"); app.run()
    uid = app.session_state["user"]["id"]
    next(b for b in app.button if b.label == "Delete this run").click(); app.run()
    assert any("This cannot be undone." in w.value for w in app.warning)
    next(b for b in app.button if b.label == "Cancel").click(); app.run()
    assert len(list_runs(db_engine(), uid)) == 1                                          # cancel keeps it
    next(b for b in app.button if b.label == "Delete this run").click(); app.run()
    next(b for b in app.button if b.label == "Yes, delete it").click(); app.run()
    assert list_runs(db_engine(), uid).empty and not app.exception
    assert any("No saved runs yet" in i.value for i in app.info)


# ---------------------------------------------------------------- demo seed
def test_seed_demo_is_idempotent_and_synthetic(engine):
    first = seed_demo.seed(engine, "TEST-demo-password-123")
    second = seed_demo.seed(engine, "a-different-password-456")                          # existing user reused as is
    assert len(first["created"]) == seed_demo.N_RUNS and second["created"] == [] and second["user_id"] == first["user_id"]
    mine = list_runs(engine, first["user_id"])
    assert len(mine) == seed_demo.N_RUNS and mine["n_rows"].sum() >= 30 and (mine["n_ranked"] > 0).all()
    assert mine["name"].str.contains("SYNTHETIC").all() and mine["name"].str.contains("synthetic asking prices").all()
    with engine.connect() as con:
        assert con.execute(select(func.count()).select_from(users)).scalar() == 1
        assert con.execute(select(users.c.is_demo)).scalar() == 1
        assert con.execute(select(func.count()).select_from(run_rows)).scalar() == mine["n_rows"].sum()
    assert authenticate(engine, seed_demo.DEMO_EMAIL, "TEST-demo-password-123")           # password not changed
    assert authenticate(engine, seed_demo.DEMO_EMAIL, "a-different-password-456") is None


def test_seed_demo_reads_the_password_only_from_the_environment(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    assert seed_demo.main() == 2                                                          # refuses without DEMO_PASSWORD
    source = (ROOT / "seed_demo.py").read_text()
    assert 'os.environ.get("DEMO_PASSWORD")' in source and "PASSWORD =" not in source


def test_seed_demo_uses_only_extract_addresses():
    import sqlite3
    con = sqlite3.connect(EXTRACT)
    known = {(a, str(z)) for a, z in con.execute("SELECT address, zip FROM parcels")}
    con.close()
    for _, c in seed_demo.synthetic_lists():
        assert set(zip(c["address"], c["zip"])) <= known and len(c) == seed_demo.ROWS_PER_RUN


def test_seed_from_environment_runs_only_when_demo_password_is_set(engine, monkeypatch, capsys):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    assert seed_demo.seed_from_environment(engine) is None
    with engine.connect() as con:
        assert con.execute(select(func.count()).select_from(users)).scalar() == 0
    monkeypatch.setenv("DEMO_PASSWORD", "TEST-demo-password-123")
    first = seed_demo.seed_from_environment(engine)
    again = seed_demo.seed_from_environment(engine)
    assert len(first["created"]) == seed_demo.N_RUNS and again["created"] == []
    out = capsys.readouterr()
    assert "TEST-demo-password-123" not in out.out + out.err                               # never logged


def test_seed_from_environment_never_breaks_the_app(engine, monkeypatch, capsys):
    monkeypatch.setenv("DEMO_PASSWORD", "too-short")                                       # refused by the password rule
    assert seed_demo.seed_from_environment(engine) is None
    assert "Demo seed skipped" in capsys.readouterr().err
    assert seed_demo.seed_from_environment(None) is None                                   # no database configured


def test_app_seeds_the_demo_runs_on_first_load_when_demo_password_is_set(monkeypatch, tmp_path):
    import streamlit as st
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("DEMO_PASSWORD", "TEST-demo-password-123")
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    st.cache_resource.clear()                                                              # a fresh "server process"
    for _ in range(2):                                                                     # second load adds nothing
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
        at.run()
        assert not at.exception and [t.value for t in at.title] == ["Ridgeline Property Analytics"]
    eng = db_engine()
    with eng.connect() as con:
        uid = con.execute(select(users.c.id).where(users.c.email == seed_demo.DEMO_EMAIL)).scalar()
    mine = list_runs(eng, uid)
    assert len(mine) == seed_demo.N_RUNS and mine["name"].str.contains("SYNTHETIC").all()
    st.cache_resource.clear()
