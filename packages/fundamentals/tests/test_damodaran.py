"""Damodaran's inputs: the fetch script's parsers on synthetic files, and the reader (damodaran.py)."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from cherrypick.fundamentals import damodaran

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_damodaran.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("fetch_damodaran", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _xlsx(rows: list[list[str]], date1904: bool = True) -> bytes:
    """A minimal workbook: inline numbers and shared strings, the way the published file stores them."""
    shared, sheet = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, val in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if val is None:
                continue
            try:
                float(val)
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            except ValueError:
                shared.append(val)
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
        sheet.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns}><workbookPr date1904="{int(date1904)}"/></workbook>')
        z.writestr(
            "xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>"
        )
        z.writestr(
            "xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{''.join(sheet)}</sheetData></worksheet>"
        )
    return buf.getvalue()


def test_the_erp_is_the_headline_column_on_the_1904_calendar(script):
    body = _xlsx(
        [
            ["Start of month", "T.Bond Rate", "ERP (T12 m with sustainable payout)", "ERP (T12m)"],
            ["44834", "0.0529", "0.042", "0.037"],  # 2026-10-01 counted from 1904
            ["1-Sep-24", "0.04", "4.05%", "0.04"],  # a text date and a text rate, as a few older rows are
            ["note", None, "words", None],
        ]
    )
    assert script.parse_erp(body) == {"2026-10-01": 0.042, "2024-09-01": 0.0405}


def test_the_ratings_page_gives_both_tables_and_its_date(script):
    row = (
        "<tr>"
        + "".join(
            f"<td>{v}</td>"
            for v in ("0.8", "1.249999", "Caa/CCC", "8.85%", "0.2", "0.299999", "Caa/CCC", "8.85%")
        )
        + "</tr>"
    )
    page = (
        "<p>Date of Analysis : Data used is as of January 2026</p>"
        f"<table><tr><td>header</td></tr>{row}</table>"
    )
    out = script.parse_ratings(page)
    assert out["as_of"] == "January 2026"
    assert out["non_financial"] == [[0.8, 1.249999, "Caa/CCC", 0.0885]] and out["financial"][0][0] == 0.2


def test_the_reader_finds_the_month_and_the_band(tmp_path):
    (tmp_path / "erp.json").write_text(json.dumps({"months": {"2026-09-01": 0.0409, "2026-10-01": 0.042}}))
    bands = [
        [-100000.0, 0.799999, "Ca2/CC", 0.1261],
        [0.8, 1.249999, "Caa/CCC", 0.0885],
        [1.25, 100000.0, "AAA", 0.004],
    ]
    (tmp_path / "ratings.json").write_text(
        json.dumps({"as_of": "January 2026", "non_financial": bands, "financial": bands})
    )
    assert damodaran.erp("2026-10-28", tmp_path) == (0.042, "2026-10-01")
    assert damodaran.erp("2026-09-15", tmp_path) == (0.0409, "2026-09-01")
    assert damodaran.erp("2020-01-01", tmp_path) is None
    assert damodaran.rating(0.85, root=tmp_path) == ("Caa/CCC", 0.0885, "January 2026")  # AAL's coverage
    assert damodaran.rating(1.2499995, root=tmp_path)[0] == "AAA"  # between rounded edges: the next band up
    assert (
        damodaran.rating(50.0, root=tmp_path)[0] == "AAA"
        and damodaran.rating(-3.0, root=tmp_path)[0] == "Ca2/CC"
    )
    assert damodaran.erp("2026-10-28", tmp_path / "none") is None
