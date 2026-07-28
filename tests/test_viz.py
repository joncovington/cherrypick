"""Tests for cherrypick.core.viz — the declarative dashboard-section contract.

The client renderer is JS (exercised end-to-end by the umbrella/live); here we cover the server-side
skeleton and that the shared style/script constants are present and non-empty.
"""

from cherrypick.core import viz


def test_card_skeleton_has_section_hooks():
    html = viz.card_skeleton_html("gex", "GEX — SPX", "/api/section/gex", refresh=15)
    assert 'data-cp-section="gex"' in html
    assert 'data-endpoint="/api/section/gex"' in html
    assert 'data-refresh="15"' in html
    # the containers the client renderer fills
    for cls in ("cpsub", "cpmetrics", "cpchart", "cpnote"):
        assert cls in html


def test_card_skeleton_escapes_untrusted_title_and_id():
    html = viz.card_skeleton_html("x<i>", "<script>alert(1)</script>", "/api/section/x", refresh=30)
    assert "<script>" not in html and "<i>" not in html
    assert "&lt;script&gt;" in html and "&lt;i&gt;" in html
    assert 'data-refresh="30"' in html


def test_style_and_script_constants_present():
    assert viz.SECTION_STYLE and ".cpmetrics" in viz.SECTION_STYLE and ".cpbar" in viz.SECTION_STYLE
    assert viz.SECTION_JS and "data-cp-section" in viz.SECTION_JS and "data-endpoint" in viz.SECTION_JS


# --- timeseries cards + inline wiring + the shared money formatter ---------------

def test_card_inline_html_bakes_the_payload():
    out = viz.card_inline_html("equity", "Suite equity", {"ok": True, "metrics": []})
    assert 'class="cpdata"' in out
    assert '"ok":true' in out
    assert "data-endpoint" not in out  # inline cards never poll


def test_card_inline_html_escapes_script_closers():
    evil = {"ok": True, "note": "</script><script>alert(1)</script>"}
    out = viz.card_inline_html("x", "t", evil)
    assert "</script><script>alert(1)" not in out
    assert r"<\/script>" in out


def test_card_skeleton_has_a_timeseries_host():
    assert 'class="cpts"' in viz.card_skeleton_html("s", "t", "/api/section/s")


def test_section_js_renders_timeseries_and_inline_mode():
    assert "renderTimeseries" in viz.SECTION_JS
    assert "script.cpdata" in viz.SECTION_JS


def test_fmt_money_sign_outside_dollar():
    assert viz.fmt_money(1234.5) == "$1,234.50"
    assert viz.fmt_money(-1234.5) == "-$1,234.50"
    assert viz.fmt_money(0) == "$0.00"
    assert viz.fmt_money(None) == "—"
    assert viz.fmt_money("junk", none="n/a") == "n/a"


def test_port_in_use_detects_a_listener_and_a_free_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert viz.port_in_use(port) is True
    # The listener is closed now — the same port reads free again.
    assert viz.port_in_use(port) is False


def test_reorder_contract_is_attribute_driven():
    # The one drag-to-reorder copy: groups and behavior are declared with data attributes,
    # so no page ships page-specific reorder code.
    for marker in ("data-cp-reorder", "data-cp-reorder-items", "data-cp-reorder-label",
                   "data-cp-reorder-defer", "data-cp-reorder-store", "reset-layout"):
        assert marker in viz.REORDER_JS
    # Unknown-keys-append: a card shipped after a layout was saved must never disappear.
    assert "indexOf" in viz.REORDER_JS
    assert ".cp-reorder-item" in viz.REORDER_STYLE


def test_cal_heat_is_the_honest_week_column_form():
    # Week columns, Mon-Fri only; an empty weekday reads 'no settled session', never a flat day.
    assert "cpCalHeat" in viz.CAL_HEAT_JS
    assert "no settled session" in viz.CAL_HEAT_JS
    assert "getUTCDay" in viz.CAL_HEAT_JS  # UTC stepping: local tz must not shift sessions
    assert ".cpcal-week" in viz.CAL_HEAT_STYLE


def test_table_component_contract():
    # Column spec + caller-owned filter state; selects populate from the UNFILTERED rows.
    for marker in ("window.cpTable", "cpTableMatches", "cpFilterActive",
                   "select", "daterange", "allRows"):
        assert marker in viz.TABLE_JS
    assert "filter-row" in viz.TABLE_STYLE


def test_table_sorting_is_opt_in_and_nulls_last():
    assert "cpTableSort" in viz.TABLE_JS
    assert "onSort" in viz.TABLE_JS
    # Nulls sort last regardless of direction — an unpriced row must never lead the table.
    assert "if (av == null) return 1;" in viz.TABLE_JS
    assert "th.sortable" in viz.TABLE_STYLE


def test_table_row_title_is_escaped():
    # opts.rowTitle(r) puts a tooltip on the row; it must escape — the MEIC log feeds it
    # free-text AI reasoning, which must never break out of the attribute.
    assert "rowTitle" in viz.TABLE_JS and "escAttr" in viz.TABLE_JS
    assert "&quot;" in viz.TABLE_JS
