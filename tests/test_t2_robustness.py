"""Kit case T2 (file robustness), on the development extract: capitalised headers, $ and k prices, a blank row,
the same house twice, extra columns."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import results_view as rv  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import load_candidates, score_batch  # noqa: E402

P = load_params(ROOT / "assumptions.json")
T2 = ('Address,Zip,Asking_Price,link,note\n'
      '2665 Cork St,30517,"$704,000",https://example.com/a,dollar and comma\n'
      '1280 Edgebrook Ln,30078,370k,https://example.com/b,k suffix\n'
      ',,,,\n'
      '2953 Sunrise Ridge Rd,30078,290000,https://example.com/c,normal\n'
      '2665 Cork St,30517,704000,https://example.com/d,same house again\n')


def scored():
    c = load_candidates(io.StringIO(T2))
    return c, score_batch(c, ROOT / "data" / "gwinnett_extract.db", P)


def test_kit_t2_file():
    c, out = scored()
    assert c.attrs["renamed"] == {"Address": "address", "Zip": "zip", "Asking_Price": "asking_price"}   # headers accepted
    assert len(out) == 5                                                                             # rows in = rows out
    by = out.set_index("row_id")
    assert by.loc[1, "asking_price_used"] == 704_000                                                 # $704,000 read
    assert (by.loc[2, "status"], by.loc[2, "reason"]) == ("unmatched", "invalid_input")              # 370k kept, reason given
    assert by.loc[3, "explanation"].startswith("Blank row in the file")
    top = out[out["top5"]]
    assert top["matched_address"].is_unique and list(top["matched_address"]).count("2665 CORK ST") == 1
    assert (by.loc[5, "status"], by.loc[5, "reason"]) == ("eliminated", "duplicate_house")
    assert "link" in out.columns and "note" in out.columns                                           # extra columns ignored, kept as-is


def test_kit_t2_table_shows_no_empty_address():
    _, out = scored()
    styler, _ = rv.results_table(out, "15%")
    table = styler.data.set_index(out["row_id"])
    assert table.loc[3, "Property address"] == "(blank row)"
    assert table.loc[5, "Status"] == "Eliminated · Same house listed twice"
    assert (table["Property address"].str.strip() != "").all()
