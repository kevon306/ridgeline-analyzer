"""The dashboard runs and reads the real scored output (headless check with Streamlit's own test tool)."""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ridgeline_core.rules import NOTICE  # noqa: E402

STATUSES = ["ranked", "eliminated", "insufficient_comps", "unmatched"]


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
    assert labels["Estimated resale value (ARV)"] == "$1,173,988" and labels["Likely + contingency"] == "$118,520"
    assert labels["Total projected investment"] == "$822,520" and labels["Projected ROI"] == "▲ +27.5%"
    assert any(w.value == NOTICE for w in at.warning)
    assert any("If renovation costs differ" in m.value and "▲ +32.9%" in m.value and "▲ +18.3%" in m.value for m in at.markdown)   # low / high band ROI
    assert any("past repeat sales" in c.value for c in at.caption)                                      # back-test reliability
    assert any("Status code: ranked" in c.value for c in at.caption)
    at.session_state["pick"] = 19                           # unmatched address: no invented figures
    at.run()
    labels = {m.label: m.value for m in at.metric}
    assert "Estimated resale value (ARV)" not in labels and "Projected ROI" not in labels
    assert any("unmatched_address" in c.value for c in at.caption)
    assert any("No county parcel matched" in i.value for i in at.info)
    assert any("Looked up as street “1 NONEXISTENT RD” in ZIP" in c.value for c in at.caption)
