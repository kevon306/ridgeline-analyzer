"""Dashboard: reads saved runs live from the database, only the logged-in user's, and answers the charter question."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import accounts  # noqa: E402
import seed_demo  # noqa: E402
from accounts import register  # noqa: E402
from dashboard_data import OUTCOMES, QUESTION, summarize, user_rows  # noqa: E402
from db import make_engine  # noqa: E402
from results_view import REASON_LABEL  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import load_candidates, score_batch  # noqa: E402
from runs import save_run  # noqa: E402

EXTRACT = ROOT / "data" / "gwinnett_extract.db"
FIXTURE = ROOT / "data" / "candidates_dev_fixture.csv"
PASSWORD = "TEST-demo-password-123"


@pytest.fixture
def engine():
    return make_engine("sqlite://")


@pytest.fixture(scope="module")
def scored():
    return score_batch(load_candidates(FIXTURE), EXTRACT, load_params(ROOT / "assumptions.json"))


def test_demo_data_gives_at_least_30_fictional_records_and_a_complete_summary(engine):
    uid = seed_demo.seed(engine, PASSWORD)["user_id"]
    df = user_rows(engine, uid)
    s = summarize(df, REASON_LABEL)
    assert s["n"] == len(df) >= 30 and s["runs"] == seed_demo.N_RUNS
    assert df["run_name"].str.contains("SYNTHETIC").all()                                    # fictional data only
    assert list(s["counts"].index) == OUTCOMES and int(s["counts"].sum()) == s["n"]          # every candidate counted once
    worth = df[(df["status"] == "ranked") & (df["roi_pct"] > 0)]
    assert int(s["counts"]["Worth pursuing"]) == len(worth) == len(s["worth"])
    assert int(s["reasons"].sum()) == int((df["status"] != "ranked").sum())                 # the rest, each with a reason
    assert list(s["worth"]["roi_pct"]) == sorted(s["worth"]["roi_pct"], reverse=True)
    assert s["answer"].startswith(f"Of {s['n']} candidates in {seed_demo.N_RUNS} saved runs, {len(worth)} (")
    assert "screened out, most often for:" in s["answer"]


def test_numbers_are_live_a_new_run_moves_them(engine, scored):
    uid = register(engine, "live@example.com", PASSWORD)["id"]
    assert summarize(user_rows(engine, uid), REASON_LABEL) == {"n": 0}
    save_run(engine, uid, "first", scored)
    one = summarize(user_rows(engine, uid), REASON_LABEL)
    save_run(engine, uid, "second", scored)
    two = summarize(user_rows(engine, uid), REASON_LABEL)
    assert one["n"] == len(scored) and two["n"] == 2 * len(scored) and two["runs"] == 2
    assert int(two["counts"]["Worth pursuing"]) == 2 * int(one["counts"]["Worth pursuing"])


def test_one_user_never_sees_another_users_rows(engine, scored):
    a = register(engine, "a@example.com", PASSWORD)["id"]
    b = register(engine, "b@example.com", PASSWORD)["id"]
    run_a = save_run(engine, a, "a's run", scored)
    save_run(engine, b, "b's run", scored.head(3))
    assert set(user_rows(engine, a)["run_name"]) == {"a's run"}
    assert set(user_rows(engine, b)["run_name"]) == {"b's run"} and len(user_rows(engine, b)) == 3
    assert user_rows(engine, b, run_a).empty                                                   # b cannot read a's run by id


def app_as_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    accounts._engine_for.clear()
    eng = accounts._engine_for("sqlite://")
    uid = seed_demo.seed(eng, PASSWORD)["user_id"]
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.session_state["user"] = {"id": uid, "email": seed_demo.DEMO_EMAIL, "display_name": "Demo investor"}
    at.switch_page("views/dashboard.py")
    at.run()
    return at, eng, uid


def test_dashboard_page_names_its_question_and_answers_it(monkeypatch, tmp_path):
    at, eng, uid = app_as_demo(monkeypatch, tmp_path)
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert QUESTION in page and "are worth pursuing" in page
    assert [m.label for m in at.metric][:2] == ["Candidates screened", "Worth pursuing"]
    assert at.metric[0].value == str(summarize(user_rows(eng, uid), REASON_LABEL)["n"])
    assert len(at.get("vega_lite_chart")) == 3                                          # outcomes, reasons, ROI spread


def test_dashboard_filters_to_one_run(monkeypatch, tmp_path):
    at, eng, uid = app_as_demo(monkeypatch, tmp_path)
    from runs import list_runs
    first = int(list_runs(eng, uid)["id"].iloc[0])
    at.selectbox(key="dash_run").set_value(first).run()
    assert not at.exception and at.metric[0].value == str(seed_demo.ROWS_PER_RUN)
    at.selectbox(key="dash_run").set_value(0).run()                                           # back to all runs
    assert at.metric[0].value == str(seed_demo.N_RUNS * seed_demo.ROWS_PER_RUN)


def test_dashboard_with_no_runs_points_to_the_screener(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    accounts._engine_for.clear()
    uid = register(accounts._engine_for("sqlite://"), "empty@example.com", PASSWORD)["id"]
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.session_state["user"] = {"id": uid, "email": "empty@example.com", "display_name": None}
    at.switch_page("views/dashboard.py")
    at.run()
    assert not at.exception and any("No saved runs yet" in i.value for i in at.info)


def test_public_home_sells_the_product_and_links_to_login(monkeypatch, tmp_path):
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    page = " ".join(m.value for m in at.markdown)
    assert not at.exception and "rl-mark" in page and "Ridgeline Property Analytics" in page        # the firm's masthead
    assert "Built for" in page and "fix-and-flip investors in Gwinnett County" in page               # who it is for
    assert page.count("rl-tile") >= 3                                                               # the benefits
    assert "Log in" in [p.label for p in at.get("page_link")]                                       # an obvious way in
