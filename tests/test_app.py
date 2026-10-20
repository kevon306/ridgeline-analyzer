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
    assert (full["Projected ROI"][full["Rank"] == ""] == "—").all()   # no ROI invented for unranked rows


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
    assert any("Within 15%, among houses the tool could value: 69%" in m.value and
               "counting houses it could not value as misses: 54%" in m.value for m in at.markdown)    # verified reliability
    assert any(m.value.startswith("**Renovation confidence: Low**") for m in at.markdown)              # no validation data yet
    assert any("Status code: ranked" in c.value for c in at.caption)
    assert any("Matched county record: 2665 CORK ST (parcel R3006A220)" in c.value for c in at.caption)   # the county pin,
    assert not any("33409449" in c.value for c in at.caption)                                             # not the internal id
    at.session_state["pick"] = 19                           # unmatched address: no invented figures
    at.run()
    labels = {m.label: m.value for m in at.metric}
    assert "Estimated resale value (ARV)" not in labels and "Projected ROI" not in labels
    assert any("unmatched_address" in c.value for c in at.caption)
    assert any("No county parcel matched" in i.value for i in at.info)
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
