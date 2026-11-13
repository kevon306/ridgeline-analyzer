"""The dashboard runs and reads the real scored output (headless check with Streamlit's own test tool)."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ridgeline_core.rules import NOTICE  # noqa: E402

STATUSES = ["ranked", "eliminated", "insufficient_comps", "unmatched"]


@pytest.fixture(autouse=True)
def evidence_db(tmp_path, monkeypatch):                  # the app's evidence database goes to a temp file, not data/
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "evidence.db"))
    return tmp_path / "evidence.db"


def example_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.session_state["user"] = {"id": 1, "email": "tester@example.com", "display_name": None}   # the screener needs a login
    at.switch_page("views/screener.py")
    at.run()
    assert not at.exception and any("Upload your candidate list" in i.value for i in at.info)   # nothing uploaded yet
    at.checkbox[0].check().run()                                                                  # tick the development example
    assert not at.exception
    return at


def test_dashboard_runs_on_the_development_example():
    at = example_app()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Processed"] == "20" and metrics["Ranked"] == "14"
    assert sum(int(metrics[k]) for k in ("Ranked", "Eliminated", "Could not value", "Could not match")) == 20
    assert any("All 20 properties" in c.value for c in at.caption)
    assert [s.value for s in at.subheader][:3] == ["Top 5 candidates by projected ROI", "All results", "How a property was evaluated"]
    for rid in (1, 15, 16, 19, 20):                       # a ranked, eliminated, could-not-value, unmatched and invalid-price row
        at.session_state["pick"] = rid
        at.run()
        assert not at.exception


def test_top_five_and_full_table_follow_the_engine_ranking():
    at = example_app()
    top, full = at.dataframe[0].value, at.dataframe[1].value
    assert top["Rank"].tolist() == ["★ 1", "★ 2", "★ 3", "★ 4", "★ 5"]
    assert top["Property address"].tolist() == ["2665 CORK ST", "1280 EDGEBROOK LN", "2953 SUNRISE RIDGE RD", "2794 CORK ST", "2800 DRAYTON HALL DR"]
    assert len(full) == 20                                  # every row accounted for
    ranks = [r for r in full["Rank"] if r]
    assert ranks[:5] == top["Rank"].tolist() and ranks[5:] == [str(i) for i in range(6, 15)]
    assert (full["Projected ROI (est.)"][full["Rank"] == ""] == "—").all()   # no ROI invented for unranked rows


def test_status_filter_shows_when_it_is_on_and_resets():
    at = example_app()
    at.multiselect(key="status_filter").set_value(["eliminated"]).run()
    assert len(at.dataframe[1].value) == 2
    assert any("Filter on" in c.value and "2 of 20" in c.value for c in at.caption)
    next(b for b in at.button if b.label == "Show all statuses").click().run()
    assert at.session_state["status_filter"] == STATUSES and len(at.dataframe[1].value) == 20
    assert not any("Filter on" in c.value for c in at.caption)


def test_detail_panel_shows_inputs_codes_and_the_renovation_notice():
    at = example_app()
    at.session_state["pick"] = 2                            # rank 1, ranked
    at.run()
    labels = {m.label: m.value for m in at.metric}
    assert labels["Estimated resale value (ARV)"] == "$1,173,988" and labels["Expected budget"] == "$118,520"
    assert labels["Total projected investment"] == "$822,520" and labels["Projected ROI"] == "▲ +27.5%"
    assert any(w.value == NOTICE for w in at.warning)
    scen = next(d.value for d in at.dataframe if "Renovation scenario" in d.value.columns)              # low / expected / high
    assert scen["Projected ROI"].tolist() == ["▲ +32.9%", "▲ +27.5%", "▲ +18.3%"] and set(scen["Basis"]) == {"Model"}
    box = next(m.value for m in at.markdown if "among houses the tool could value" in m.value)     # verified reliability (T8)
    assert "Within 15%, among houses the tool could value: 69.2%** (2,595 of 3,749)" in box
    assert "Could be valued (coverage): 77.8%** (3,749 of 4,817)" in box
    assert "Within 15%, counting houses it could not value as misses: 53.9%** (2,595 of 4,817)" in box
    assert any(m.value.startswith("**Renovation confidence: Low**") for m in at.markdown)              # no validation data yet
    assert any("Status code: ranked" in c.value for c in at.caption)
    assert any("Matched county record: 2665 CORK ST (parcel R3006A220)" in c.value for c in at.caption)   # the county pin,
    assert not any("33409449" in c.value for c in at.caption)                                             # not the internal id
    at.session_state["pick"] = 19                           # unmatched address: no invented figures
    at.run()
    labels = {m.label: m.value for m in at.metric}
    assert "Estimated resale value (ARV)" not in labels and "Projected ROI" not in labels
    assert any("unmatched_address" in c.value for c in at.caption)
    box = [i.value for i in at.info if "Could not match" in i.value]
    assert box == ["**Could not match · Not found in the county's single-family records.**"]          # said once, not twice
    assert labels["Reason"] == "Not found in the county's single-family records"            # display label only
    assert any("Could not match · Not found in the county's single-family records." in i.value for i in at.info)
    assert any("Looked up as street “1 NONEXISTENT RD” in ZIP" in c.value for c in at.caption)


def test_evidence_panel_shows_saved_inspection_completeness_and_feedback(evidence_db):
    from ridgeline_core import evidence as ev
    from ridgeline_core.renovation import scope_budget
    con = ev.connect(evidence_db)                         # TEST evidence for 2665 CORK ST (parcel R3006A220)
    pid = ev.property_id(con, "R3006A220", "2665 CORK ST", "30517")
    ev.add_inspection(con, pid, "2026-05-01", [{"component": "Roof", "condition": "replace", "evidence_ref": "TEST photo 1"},
                                               {"component": "HVAC", "condition": "good", "verified_age_years": 2, "age_source": "TEST label"}])
    ev.add_estimate(con, pid, "scope", scope_budget([{"category": "Roof", "low": 9_000, "expected": 11_000, "high": 14_000}], 0.1),
                    "bands-v1+scope", created_on="2026-05-02")
    ev.add_cost_record(con, pid, "Roof", "final", 12_500, "TEST roofer", "verified", "2026-07-01", evidence_ref="TEST inv 9")
    con.close()
    at = example_app()
    at.session_state["pick"] = 2
    at.run()
    assert not at.exception
    comp = next(d.value for d in at.dataframe if "impact" in d.value.columns).set_index("item")
    assert comp.loc["Roof", "status"] == "inspected" and comp.loc["Kitchen", "status"] == "unknown"
    assert any("Roof** (elevated)" in m.value and "replace" in m.value for m in at.markdown)            # inspection drives the flag
    assert any("HVAC** (documented)" in m.value for m in at.markdown)
    assert any("vs verified final cost" in m.value and "12,500" in m.value for m in at.markdown)        # feedback


def test_detail_panel_tables_convert_to_arrow_without_streamlits_automatic_fixes():
    import logging
    hits = []

    class Catch(logging.Handler):
        def emit(self, record):
            if "Serialization of dataframe to Arrow table was unsuccessful" in record.getMessage():
                hits.append(record.getMessage())

    handler = Catch()
    loggers = [logging.getLogger(n) for n in ["streamlit"] + [k for k in logging.root.manager.loggerDict if k.startswith("streamlit")]]
    for lg in loggers:
        lg.addHandler(handler)
    try:
        at = example_app()
        for rid in (2, 14, 16, 19, 20):                   # ranked, eliminated, could not value, unmatched, invalid price
            at.session_state["pick"] = rid
            at.run()
            assert not at.exception
    finally:
        for lg in loggers:
            lg.removeHandler(handler)
    assert hits == []                                     # e.g. the completeness table's mixed number/blank value column


def test_estimated_columns_say_est_and_the_explanation_is_on_the_main_page():
    at = example_app()
    top, full = at.dataframe[0].value, at.dataframe[1].value
    estimated = ["Resale value (est.)", "Renovation (est.)", "Projected profit (est.)", "Projected ROI (est.)"]
    for table in (top, full):
        assert set(estimated) <= set(table.columns)
        assert not {"Resale value", "Renovation", "Projected profit", "Projected ROI"} & set(table.columns)   # no unmarked copies
        assert "Asking price" in table.columns                                                             # the one listed fact
    assert top["Based on"].tolist() == ["Based on 6 sales", "Based on 3 sales", "Based on 30 sales", "Based on 3 sales", "Based on 3 sales"]
    captions = [c.value for c in at.caption]
    assert captions.count("Asking price comes from the listing; everything marked est. is our estimate.") == 2   # under both tables
    assert "#### How these numbers are calculated" in [m.value for m in at.markdown]
    body = next(m.value for m in at.markdown if "Worked example" in m.value)
    assert "ROI = projected profit ÷ (asking price + renovation)" in body and "2665 CORK ST" in body
    assert "\\$225,869" in body and "27.5%" in body                                  # the #1 listing's own figures


def test_no_loss_note_when_all_five_are_projected_to_make_money():
    at = example_app()                                    # dev example: the top five are all positive
    assert not any("projected to lose money" in w.value for w in at.warning)


def test_a_list_with_no_rows_does_not_crash_the_results(tmp_path, monkeypatch):
    monkeypatch.setenv("RIDGELINE_EVIDENCE_DB", str(tmp_path / "ev.db"))
    monkeypatch.setenv("RIDGELINE_TEST_ROOT", str(ROOT))      # the page function runs on its own, without this file's globals

    def page():
        import io
        import os
        import results_view as rv
        from ridgeline_core.arv import load_params
        from ridgeline_core.pipeline import load_candidates, score_batch
        root = os.environ["RIDGELINE_TEST_ROOT"]
        P = load_params(f"{root}/assumptions.json")
        scored = score_batch(load_candidates(io.StringIO("address,zip,asking_price")), f"{root}/data/gwinnett_extract.db", P)
        ctx = rv.context(P, {}, "test")
        rv.render_counts(scored); rv.render_top5(scored, ctx); rv.render_all_results(scored, ctx); rv.render_detail(scored, ctx)

    at = AppTest.from_function(page, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("No properties to show." == c.value for c in at.caption)


def test_screener_stops_cleanly_on_a_header_only_file():
    src = (ROOT / "views" / "screener.py").read_text()
    guard = src.index("if candidates.empty:")
    assert guard < src.index("scored, index = score_list(") and "no property rows" in src[guard:guard + 200]
