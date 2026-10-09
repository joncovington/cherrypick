"""The 2026-10-12 GEX arithmetic change is journaled once, book-wide, as a non-bounding surface break."""

from cherrypick.core import regimecuts

from cherrypick.flies import db as dbmod
from cherrypick.flies import paper_loop


def test_the_one_gamma_change_is_journaled_once_and_bounds_no_era(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    paper_loop._note_gex_one_gamma(conn)
    paper_loop._note_gex_one_gamma(conn)
    rows = [
        r for r in dbmod.measurement_breaks(conn) if r["break_date"] == "2026-10-12" and r["scope"] == "*"
    ]
    assert len(rows) == 1 and rows[0]["kind"] == "gex_surface"
    assert "strike_gamma" in rows[0]["reason"]
    assert "gex_surface" in regimecuts.NON_BOUNDING_KINDS  # tags change meaning; no arm's decisions do
