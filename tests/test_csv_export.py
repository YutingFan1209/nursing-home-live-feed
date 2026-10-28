"""
The site's CSV and Excel downloads (dashboard/frontend/src/export.js), run
under Node and checked byte by byte.

Excel for Mac read the BOM-less CSV as Mac Roman, so an em dash came out as
"‚Äî" (2026-10-28). The files are also written to tests/output/ for the
manual check: open excel_check.csv and excel_check.xlsx in Excel for Mac,
Excel for Windows and Google Sheets; row 2 should read
B2 Café – São Paulo, C2 202603287433, D2 015372 (xlsx; the CSV keeps the raw
value, which Excel shows as 15372), E2 the text =1+1, F2 the date 2026-10-23.
"""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXPORT_JS = ROOT / "dashboard" / "frontend" / "src" / "export.js"
OUT = ROOT / "tests" / "output"

ROWS = [
    ["id", "text", "filing_number", "ccns", "formula", "date", "beds"],
    ["31e7d16e", "Café – São Paulo", "202603287433", "015372", "=1+1", "2026-10-23", 120],
    ["x2", 'say "hi", then\r\nleave', "OH00302602697", "065253 065247", "+SUM(A1)", "", None],
    ["x3", "@cmd", "", "", "-2+3", "", 0],
]

RUNNER = """
import { toCsv, toXlsx } from %s;
import { writeFileSync } from "node:fs";
const rows = JSON.parse(process.argv[1]);
writeFileSync(process.argv[2], toCsv(rows));
writeFileSync(process.argv[3], await toXlsx(rows, { dateColumns: ["date"] }));
"""


@pytest.fixture(scope="module")
def files():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    OUT.mkdir(exist_ok=True)
    csv_path, xlsx_path = OUT / "excel_check.csv", OUT / "excel_check.xlsx"
    subprocess.run([node, "--input-type=module", "-e", RUNNER % json.dumps(EXPORT_JS.as_uri()),
                    json.dumps(ROWS), str(csv_path), str(xlsx_path)], check=True, cwd=OUT)
    return csv_path.read_bytes(), xlsx_path


def test_csv_starts_with_a_bom_and_round_trips_as_utf8(files):
    raw, _ = files
    assert raw[:3] == b"\xef\xbb\xbf"
    assert "Café – São Paulo".encode("utf-8") in raw
    assert b"\xe2\x80\x93" in raw                       # en dash, encoded once
    assert b"\xc3\xa2\xc2\x80" not in raw               # not double-encoded
    text = raw.decode("utf-8-sig")
    assert text.startswith("id,text,")


def test_csv_ids_stay_as_written(files):
    raw, _ = files
    line = raw.decode("utf-8-sig").split("\r\n")[1]
    assert line.startswith("31e7d16e,Café – São Paulo,202603287433,015372,")


def test_csv_neutralizes_formula_starts_and_quotes_line_breaks(files):
    import csv
    import io
    raw, _ = files
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    assert rows[1][4] == "'=1+1"
    assert rows[2][4] == "'+SUM(A1)" and rows[3][1] == "'@cmd" and rows[3][4] == "'-2+3"
    assert rows[2][1] == 'say "hi", then\r\nleave'
    assert rows[1][6] == "120" and rows[3][6] == "0"     # numbers are not touched
    assert len(rows) == 4


def test_xlsx_keeps_ids_as_text_dates_as_dates_and_formulas_as_text(files):
    openpyxl = pytest.importorskip("openpyxl")
    _, path = files
    assert zipfile.ZipFile(path).testzip() is None
    ws = openpyxl.load_workbook(path).active
    assert ws["B2"].value == "Café – São Paulo"
    for ref, want in (("A2", "31e7d16e"), ("C2", "202603287433"), ("D2", "015372"), ("E2", "=1+1"), ("D3", "065253 065247")):
        assert ws[ref].data_type == "s" and ws[ref].value == want, ref
    assert ws["F2"].is_date and ws["F2"].value.date().isoformat() == "2026-10-23"
    assert ws["G2"].data_type == "n" and ws["G2"].value == 120
    assert ws["B3"].value == 'say "hi", then\nleave'      # XML stores a line break as \n
    assert ws["F3"].value is None and ws["G3"].value is None
