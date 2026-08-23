from pathlib import Path

import pytest
from kb_mcp.parse import parse_file, SUPPORTED


def test_parses_markdown(tmp_path: Path):
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nBody paragraph about OCPP error 42.", encoding="utf-8")
    segs = parse_file(f)
    assert segs and any("OCPP error 42" in s["text"] for s in segs)
    assert all("location" in s for s in segs)


def test_parses_txt_and_html(tmp_path: Path):
    (tmp_path / "a.txt").write_text("plain text body", encoding="utf-8")
    (tmp_path / "b.html").write_text("<h1>H</h1><p>html body</p>", encoding="utf-8")
    assert "plain text body" in parse_file(tmp_path / "a.txt")[0]["text"]
    assert "html body" in parse_file(tmp_path / "b.html")[0]["text"]


def test_unsupported_extension_returns_empty(tmp_path: Path):
    (tmp_path / "x.bin").write_bytes(b"\x00\x01")
    assert parse_file(tmp_path / "x.bin") == []
    assert ".pdf" in SUPPORTED and ".md" in SUPPORTED


def _make_docx(path, paragraphs, table_rows=None, heading=None):
    from docx import Document
    doc = Document()
    if heading:
        doc.add_heading(heading, level=1)
    for p in paragraphs:
        doc.add_paragraph(p)
    if table_rows:
        t = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    doc.save(path)


def test_parses_docx_paragraphs_and_tables(tmp_path: Path):
    f = tmp_path / "runbook.docx"
    _make_docx(f, ["Connector lock failure recovery."],
               table_rows=[["Code", "Meaning"], ["E42", "comms lost"]],
               heading="Stuck Connector")
    segs = parse_file(f)
    body = " ".join(s["text"] for s in segs)
    assert "Connector lock failure recovery." in body
    assert "E42" in body and "comms lost" in body
    assert any(s["location"] == "Stuck Connector" for s in segs)


def test_empty_docx_yields_no_segments(tmp_path: Path):
    f = tmp_path / "empty.docx"
    _make_docx(f, [])
    assert parse_file(f) == []


def test_docx_is_supported(tmp_path: Path):
    assert ".docx" in SUPPORTED


def test_each_table_gets_the_heading_it_sits_under(tmp_path: Path):
    """Regression: tables were previously tagged with the last heading in the
    whole document, so a table under one section was cited as another."""
    from docx import Document

    doc = Document()
    doc.add_heading("Section A", level=1)
    t1 = doc.add_table(rows=1, cols=2)
    t1.cell(0, 0).text = "A-Code"
    t1.cell(0, 1).text = "value under A"
    doc.add_heading("Section B", level=1)
    t2 = doc.add_table(rows=1, cols=2)
    t2.cell(0, 0).text = "B-Code"
    t2.cell(0, 1).text = "value under B"
    f = tmp_path / "sections.docx"
    doc.save(f)

    segs = parse_file(f)
    by_text = {s["text"].split(" | ")[0]: s["location"] for s in segs}
    assert by_text["A-Code"] == "Section A"
    assert by_text["B-Code"] == "Section B"


def test_merged_cells_are_not_duplicated(tmp_path: Path):
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=1, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "Merged Header"
    table.cell(0, 2).text = "Col3"
    f = tmp_path / "merged.docx"
    doc.save(f)

    segs = parse_file(f)
    assert segs[0]["text"] == "Merged Header | Col3"


def test_parses_xlsx_one_segment_per_sheet(tmp_path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    s1 = wb.active
    s1.title = "Faults"
    s1.append(["Code", "Meaning"])
    s1.append(["E42", "comms lost"])
    s2 = wb.create_sheet("Sites")
    s2.append(["Site", "City"])
    s2.append(["QNCH-1", "Pune"])
    f = tmp_path / "matrix.xlsx"
    wb.save(f)

    segs = parse_file(f)
    locations = {s["location"] for s in segs}
    assert locations == {"Faults", "Sites"}
    faults = next(s for s in segs if s["location"] == "Faults")
    assert "Code | Meaning" in faults["text"]
    assert "E42 | comms lost" in faults["text"]


def test_empty_xlsx_sheet_produces_no_segment(tmp_path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "Blank"
    f = tmp_path / "blank.xlsx"
    wb.save(f)
    assert parse_file(f) == []


def test_parses_csv_and_repeats_header(tmp_path: Path):
    f = tmp_path / "tickets.csv"
    f.write_text("id,summary\nQCH-1,charger offline\nQCH-2,gun stuck\n", encoding="utf-8")
    segs = parse_file(f)
    assert len(segs) == 1
    assert segs[0]["location"] == "tickets.csv"
    assert "id | summary" in segs[0]["text"]
    assert "QCH-2 | gun stuck" in segs[0]["text"]


def test_spreadsheet_extensions_supported(tmp_path: Path):
    assert ".xlsx" in SUPPORTED and ".csv" in SUPPORTED


def test_uncalculated_formula_cells_are_not_silently_dropped(tmp_path: Path):
    """A workbook never opened in Excel has no cached formula results, so
    data_only=True returns None for them. Indexing that as blank would lose a
    whole column of a calculated matrix without any warning."""
    from openpyxl import Workbook
    wb = Workbook()
    s = wb.active
    s.title = "Matrix"
    s.append(["A", "B", "Sum"])
    s.append([3, 4, "=A2+B2"])
    f = tmp_path / "calc.xlsx"
    wb.save(f)

    segs = parse_file(f)
    assert "=A2+B2" in segs[0]["text"]


def test_cell_text_prefers_a_cached_value_over_the_formula():
    from kb_mcp.parse import _cell_text
    assert _cell_text(7, "=A2+B2") == "7"
    assert _cell_text(None, "=A2+B2") == "=A2+B2"
    assert _cell_text(None, None) == ""
    assert _cell_text(0, None) == "0"


def test_phantom_trailing_columns_are_trimmed(tmp_path: Path):
    """read_only mode inflates the used range to include cells that were
    written and later cleared, padding every row with empty columns."""
    from openpyxl import Workbook
    wb = Workbook()
    s = wb.active
    s.title = "Sparse"
    s.append(["Code", "Meaning"])
    s["ZZ500"] = "temp"
    s["ZZ500"] = None
    f = tmp_path / "sparse.xlsx"
    wb.save(f)

    segs = parse_file(f)
    assert segs[0]["text"] == "Code | Meaning"


def test_csv_exported_from_excel_has_a_clean_header(tmp_path: Path):
    f = tmp_path / "excel-export.csv"
    f.write_text("id,summary\nQCH-1,offline\n", encoding="utf-8-sig")
    segs = parse_file(f)
    assert segs[0]["text"].startswith("id | summary")
    assert "﻿" not in segs[0]["text"]


def test_first_workbook_is_closed_when_the_second_open_fails(tmp_path: Path, monkeypatch):
    """The two loads are opened inside the try, so a failure on the second
    does not leak the first past the finally."""
    import openpyxl
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Code", "Meaning"])
    f = tmp_path / "leak.xlsx"
    wb.save(f)

    real_load = openpyxl.load_workbook
    closed, calls = [], {"n": 0}

    def flaky_load(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise MemoryError("second load failed")
        book = real_load(*args, **kwargs)
        real_close = book.close

        def tracking_close():
            closed.append(book)
            real_close()

        book.close = tracking_close
        return book

    monkeypatch.setattr(openpyxl, "load_workbook", flaky_load)

    with pytest.raises(MemoryError):
        parse_file(f)

    assert len(closed) == 1, "the first workbook must be closed when the second open fails"
