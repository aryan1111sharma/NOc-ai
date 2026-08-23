from kb_mcp.chunk import chunk_text, chunk_tabular


def test_short_text_is_one_chunk():
    out = chunk_text("hello world", max_chars=100, overlap_chars=10)
    assert len(out) == 1
    assert out[0] == "hello world"


def test_long_text_splits_with_overlap():
    para = ("A" * 500 + "\n\n") * 6  # ~3000 chars across paragraphs
    out = chunk_text(para, max_chars=1000, overlap_chars=100)
    assert len(out) >= 3
    assert all(len(c) <= 1000 for c in out)
    assert out[0][-50:] in out[1]


def test_empty_text_yields_no_chunks():
    assert chunk_text("   ", max_chars=100, overlap_chars=10) == []


def test_tabular_chunks_all_carry_the_header():
    header = "id | summary"
    body = "\n".join(f"QCH-{i} | charger {i} offline at site" for i in range(400))
    chunks = chunk_tabular(f"{header}\n{body}", max_chars=800, overlap_chars=0)
    assert len(chunks) > 1
    assert all(c.startswith(header) for c in chunks)


def test_tabular_single_chunk_does_not_duplicate_header():
    text = "id | summary\nQCH-1 | offline"
    chunks = chunk_tabular(text, max_chars=800, overlap_chars=0)
    assert chunks == [text]


def test_tabular_empty_input_yields_nothing():
    assert chunk_tabular("", max_chars=800, overlap_chars=0) == []
