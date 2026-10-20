"""Dashboard panel for renovation evidence: property facts, inspection form, completeness report, recorded estimates,
quotes / invoices / final costs, and estimate-vs-actual feedback. Writes go to the evidence database
(ridgeline_core.evidence); nothing here changes the ranking."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from ridgeline_core import evidence as ev
from ridgeline_core.renovation import CATEGORIES, MODEL_VERSION


FACT_LABEL = {"living_area": "Living area (sq ft)", "year_built": "Year built", "bedrooms": "Bedrooms", "bathrooms": "Bathrooms",
              "property_type": "Property type"}
FACT_FIELD = {v: k for k, v in FACT_LABEL.items()}


def evidence_path() -> Path:
    """RIDGELINE_EVIDENCE_DB if set, else the repository's data folder when writable, else a temp folder."""
    if os.environ.get("RIDGELINE_EVIDENCE_DB"):
        return Path(os.environ["RIDGELINE_EVIDENCE_DB"])
    return ev.DB_PATH if os.access(ev.DB_PATH.parent, os.W_OK) else Path(tempfile.gettempdir()) / ev.DB_PATH.name


def storage_notice(path: Path) -> str:
    return (f"Evidence is saved to {path.name} on the machine running the app. On Streamlit Community Cloud that file is "
            "erased whenever the app restarts, so download a backup after each session (sidebar) and upload it to restore.")


def _money(x):
    return "—" if x is None or pd.isna(x) else (f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}")


def render(con: sqlite3.Connection, r: pd.Series, county: dict, pin: str | None, model: dict | None, scope: dict | None,
           conf_label: str):
    """Everything below the renovation estimate for one matched property."""
    if not pin:
        st.caption("Evidence can be recorded once the property is matched to a county parcel number.")
        return
    pid = ev.find_property(con, pin)                       # a property row is created only when something is saved
    findings, facts = (ev.latest_findings(con, pid), ev.facts_for(con, pid)) if pid else ({}, {})

    def ensure():
        return pid or ev.property_id(con, pin, str(r.get("matched_address") or ""), str(r.get("zip") or ""))
    key = f"ev_{pin}"

    with st.expander("Property data completeness: what is verified, missing or uncertain"):
        comp = ev.completeness(county, facts, findings)
        st.dataframe(comp.assign(priority=comp["priority"].map(lambda p: "" if p is None else str(int(p)))),
                     hide_index=True, width="stretch", height=(len(comp) + 1) * 35 + 3,
                     column_config={"impact": st.column_config.Column(help="How much this item can move a renovation budget "
                                                                      "(a suggested order, not a measured ranking).")})
        todo = comp[comp["priority"].notna()].head(3)["item"].tolist()
        st.caption(("Collect next: " + ", ".join(todo) + ".") if todo else "Every listed item is recorded.")

    with st.expander("Record property facts (bedrooms, bathrooms, size, type)"):
        with st.form(f"{key}_facts"):
            rows = st.data_editor(pd.DataFrame({"field": [FACT_LABEL[f] for f in ev.FACT_FIELDS], "value": [""] * len(ev.FACT_FIELDS),
                                                "verified": [False] * len(ev.FACT_FIELDS), "source": [""] * len(ev.FACT_FIELDS)}),
                                  hide_index=True, width="stretch", disabled=["field"],
                                  column_config={"verified": st.column_config.CheckboxColumn(help="Tick only if you checked it yourself "
                                                                                             "(walkthrough, appraisal, listing photos with measurements)."),
                                                 "source": st.column_config.TextColumn(help="Required: where the value came from.")})
            if st.form_submit_button("Save facts"):
                saved, errors = 0, []
                for _, f in rows.iterrows():
                    if str(f["value"]).strip():
                        if not str(f["source"]).strip():
                            errors.append(f"{f['field']}: a source is required")
                            continue
                        ev.add_fact(con, ensure(), FACT_FIELD[f["field"]], str(f["value"]).strip(), "verified" if f["verified"] else "unverified", f["source"])
                        saved += 1
                (st.error if errors else st.success)("; ".join(errors) if errors else f"Saved {saved} fact(s).")

    with st.expander("Inspection and repair scope"):
        st.caption("Leave a component at 'Unknown / not inspected' unless you saw it. Unknown stays unknown: nothing is assumed "
                   "to need replacement. A verified age needs a source (receipt, permit, label, inspector).")
        with st.form(f"{key}_insp"):
            a, b = st.columns(2)
            when = a.date_input("Inspection date", date.today())
            who = b.text_input("Inspector or person who looked")
            labels = [ev.CONDITION_LABEL[c] for c in ev.CONDITIONS]
            prev = {c: findings.get(c, {}) for c in ev.COMPONENTS}
            grid = st.data_editor(pd.DataFrame({
                "component": list(ev.COMPONENTS),
                "condition": [ev.CONDITION_LABEL[prev[c].get("condition", "unknown")] for c in ev.COMPONENTS],
                "verified_age_years": [prev[c].get("verified_age_years") for c in ev.COMPONENTS],
                "age_source": [prev[c].get("age_source") or "" for c in ev.COMPONENTS],
                "notes": [prev[c].get("notes") or "" for c in ev.COMPONENTS],
                "evidence_ref": [prev[c].get("evidence_ref") or "" for c in ev.COMPONENTS]}),
                hide_index=True, width="stretch", disabled=["component"], height=(len(ev.COMPONENTS) + 1) * 35 + 3,
                column_config={"condition": st.column_config.SelectboxColumn(options=labels, required=True),
                               "verified_age_years": st.column_config.NumberColumn("Verified age (yrs)", min_value=0),
                               "evidence_ref": st.column_config.TextColumn("Evidence (photo, report page)")})
            if st.form_submit_button("Save inspection"):
                back = {v: k for k, v in ev.CONDITION_LABEL.items()}
                fs = [{"component": g["component"], "condition": back[g["condition"]], "verified_age_years": g["verified_age_years"],
                       "age_source": g["age_source"], "notes": g["notes"], "evidence_ref": g["evidence_ref"]} for _, g in grid.iterrows()]
                missing_src = [f["component"] for f in fs if pd.notna(f["verified_age_years"]) and not str(f["age_source"]).strip()]
                if missing_src:
                    st.error("A verified age needs a source: " + ", ".join(missing_src))
                else:
                    ev.add_inspection(con, ensure(), when.isoformat(), fs, who)
                    st.success("Inspection saved. Risk flags and completeness now use it.")

    with st.expander("Estimates, quotes and final costs (feedback)"):
        st.caption("Record the estimate BEFORE work starts; only an estimate made before the final costs is compared with them. "
                   "Quotes and unverified invoices are stored but never counted as actual outcomes.")
        c1, c2 = st.columns(2)
        if model and c1.button("Record the model estimate now", key=f"{key}_rec_m"):
            ev.add_estimate(con, ensure(), "model", model, MODEL_VERSION, confidence=conf_label)
            st.success("Model estimate recorded.")
        if scope and c2.button("Record your scope estimate now", key=f"{key}_rec_s"):
            ev.add_estimate(con, ensure(), "scope", scope, MODEL_VERSION + "+scope", confidence=conf_label)
            st.success("Your scope estimate recorded.")
        pid = ev.find_property(con, pin)
        est = pd.read_sql("SELECT created_on, basis, low_total, expected_total, high_total, confidence FROM estimates "
                          "WHERE property_id = ? ORDER BY created_on, estimate_id", con, params=(pid or -1,))
        if len(est):
            st.dataframe(est, hide_index=True, width="stretch")
        with st.form(f"{key}_costs"):
            n = 6
            costs = st.data_editor(pd.DataFrame({"category": [None] * n, "kind": ["quote"] * n, "amount": [None] * n,
                                                 "materials": [None] * n, "labor": [None] * n, "overhead": [None] * n,
                                                 "source": [""] * n, "evidence_ref": [""] * n, "verified": [False] * n,
                                                 "date": [date.today().isoformat()] * n}),
                                   hide_index=True, width="stretch", num_rows="dynamic",
                                   column_config={"category": st.column_config.SelectboxColumn(options=CATEGORIES),
                                                  "kind": st.column_config.SelectboxColumn(options=list(ev.COST_KINDS), required=True,
                                                                                           help="final = what the work actually cost when finished"),
                                                  "verified": st.column_config.CheckboxColumn(help="Backed by an invoice, closing statement or receipts; needs an evidence reference.")})
            if st.form_submit_button("Save costs"):
                saved, errors = 0, []
                for _, c in costs.iterrows():
                    if not c["category"] or pd.isna(c["amount"]):
                        continue
                    if not str(c["source"]).strip():
                        errors.append(f"{c['category']}: a source is required")
                        continue
                    try:
                        ev.add_cost_record(con, ensure(), c["category"], c["kind"], c["amount"], str(c["source"]).strip(),
                                           "verified" if c["verified"] else "unverified", str(c["date"]),
                                           c["materials"], c["labor"], c["overhead"], str(c["evidence_ref"]).strip())
                        saved += 1
                    except (ValueError, sqlite3.IntegrityError) as e:
                        errors.append(f"{c['category']}: {e}")
                if errors:
                    st.error("; ".join(errors))
                st.success(f"Saved {saved} cost record(s).") if saved else None
        pid = ev.find_property(con, pin)
        fb = ev.feedback(con, pid) if pid else {"status": "no verified final costs yet"}
        if fb["status"] != "ok":
            st.caption(f"Feedback: {fb['status']}.")
        else:
            t = fb["total"]
            text = (f"**Estimate ({fb['basis']}, {fb['estimated_on']}) vs verified final cost:** expected {_money(t['expected'])}, "
                    f"actual {_money(t['actual'])}, error {_money(t['error'])}"
                    + (f" ({t['pct_error']*100:+.1f}%)" if t["pct_error"] is not None else "")
                    + (f"; actual {'inside' if t['in_range'] else 'outside'} the predicted range." if t["in_range"] is not None else "."))
            st.markdown(text.replace("$", "\\$"))                       # unescaped $...$ renders as math
            c = fb["categories"].copy()
            for col in ("low", "expected", "high", "actual", "error", "abs_error"):
                if col in c:
                    c[col] = c[col].map(lambda v: _money(v) if pd.notna(v) else "—")
            if "pct_error" in c:
                c["pct_error"] = c["pct_error"].map(lambda v: f"{v*100:+.1f}%" if pd.notna(v) else "—")
            if "in_range" in c:
                c["in_range"] = c["in_range"].map(lambda v: "—" if v is None or pd.isna(v) else ("yes" if v else "no"))
            st.dataframe(c, hide_index=True, width="stretch")
            st.caption("Category figures are base costs; the total includes contingency, which is a project-level reserve.")


def sidebar_backup(con: sqlite3.Connection, path: Path):
    """Download / upload the whole evidence database, so it survives a Cloud restart."""
    with st.expander("Renovation evidence backup"):
        st.caption(storage_notice(path))
        st.download_button("Download evidence backup (JSON)", ev.export_json(con), file_name="ridgeline_evidence.json",
                           mime="application/json")
        up = st.file_uploader("Restore from a backup", type="json", key="evidence_restore")
        if up is not None and st.button("Replace current evidence with this backup"):
            try:
                counts = ev.import_json(con, up.getvalue().decode("utf-8"))
                st.success("Restored: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
            except (ValueError, sqlite3.Error) as e:
                st.error(f"Backup not restored: {e}")
