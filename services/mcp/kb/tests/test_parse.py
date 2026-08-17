from pathlib import Path
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
