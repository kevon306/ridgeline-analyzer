"""The dashboard runs and reads the real scored output (headless check with Streamlit's own test tool)."""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_runs_on_the_development_example():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    assert not at.exception and any("Upload your candidate list" in i.value for i in at.info)   # nothing uploaded yet
    at.checkbox[0].check().run()                                                                  # tick the development example
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Processed"] == "20" and metrics["Ranked"] == "14"
    assert [s.value for s in at.subheader][:2] == ["Top 5 candidates by projected ROI", "All results"]
    for rid in (1, 15, 16, 19, 20):                       # a ranked, eliminated, could-not-value, unmatched and invalid-price row
        at.session_state["pick"] = rid
        at.run()
        assert not at.exception
