"""Upload behaviour: one file, many files, size ceilings, and per-KB isolation.

Bulk upload is a sequence of single-file `upload_document()` calls -- that is the whole
design (see files/client/kbUpload.ts): the UI queues the selection and the pipeline is
untouched, so "many files" must behave exactly like "one file, repeated". These tests
drive that sequence directly, which is what makes them a test of the bulk path rather
than of the UI's loop.

Every upload here goes through the real pipeline: parse -> chunk -> embed -> upsert.
Only the embedder is a double (FakeEmbedder), so no model download is needed; the
parsers, the chunker, `ingest()` and Qdrant are the production ones.
"""
import base64

import pytest
from qdrant_client import QdrantClient

from kb_mcp import manage
from kb_mcp.config import settings
from kb_mcp.manage import ManageError, upload_document, writable_scope
from kb_mcp.scopes import parse_scopes
from kb_mcp.store import StoreRegistry
from tests.fakes import FakeEmbedder

#: Two confidential scopes, i.e. two independent knowledge bases, plus one shared
#: non-confidential scope. That is the shape the KB registry produces, and it is what
#: makes the isolation assertion below meaningful: `noc` and `finance` get separate
#: Qdrant collections.
CFG = parse_scopes(
    {
        "scopes": {"ev-core": {}, "noc": {"confidential": True}, "finance": {"confidential": True}},
        "profiles": {
            "noc": {"scopes": ["noc"], "description": "NOC runbooks."},
            "finance": {"scopes": ["finance"], "description": "Finance policy."},
        },
    },
    source="test",
)


@pytest.fixture
def kb(tmp_path):
    """A working knowledge corpus: real folders, real store, fake embedder."""
    for scope in CFG.scopes.values():
        (tmp_path / scope.folder).mkdir(parents=True, exist_ok=True)

    embedder = FakeEmbedder()
    registry = StoreRegistry(
        QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=embedder.dim
    )
    registry.ensure_all()

    class Harness:
        knowledge_dir = tmp_path
        config = CFG
        store_registry = registry

        def upload(self, filename, content="", *, profile="noc", content_b64=""):
            return upload_document(
                filename,
                content,
                scope=writable_scope(CFG.profiles[profile], CFG),
                content_b64=content_b64,
                knowledge_dir=tmp_path,
                config=CFG,
                registry=registry,
                embedder=embedder,
                settings=settings,
            )

        def sources(self, scope="noc"):
            return registry.for_scope(scope).known_sources()

    return Harness()


def _pdf(text: str) -> bytes:
    """A real, single-page PDF. Built with the same library the parser reads it with,
    so this exercises the actual .pdf branch rather than a stand-in."""
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 100), text)
    return doc.tobytes()


def _b64_of_size(size: int) -> str:
    """Base64 for exactly `size` decoded bytes, by arithmetic rather than by encoding
    a hundred megabytes. The padding matters: it is what makes the decoded length come
    out exact instead of up to two bytes over."""
    full, rest = divmod(size, 3)
    chars = full * 4 + (0 if rest == 0 else 4)
    padding = "" if rest == 0 else ("==" if rest == 1 else "=")
    return "A" * (chars - len(padding)) + padding


# ---------------------------------------------------------------------------
# Single-file upload: the behaviour bulk upload must not change
# ---------------------------------------------------------------------------


def test_a_single_text_upload_is_written_and_indexed(kb):
    result = kb.upload("runbook.md", "# Reboot\n\nPower-cycle the charger, then re-check OCPP.")

    assert result["uploaded"] == "runbook.md"
    assert result["scope"] == "noc"
    assert result["replaced"] is False
    assert result["index"]["indexed_docs"] == 1
    assert (kb.knowledge_dir / "noc" / "runbook.md").is_file()
    assert "noc/runbook.md" in kb.sources()


def test_a_single_binary_upload_is_written_and_indexed(kb):
    payload = _pdf("Fault 42 means loss of communication with the charge point.")

    result = kb.upload("manual.pdf", content_b64=base64.b64encode(payload).decode())

    assert result["uploaded"] == "manual.pdf"
    assert result["bytes"] == len(payload)
    assert result["index"]["indexed_docs"] == 1
    assert "noc/manual.pdf" in kb.sources()


def test_re_uploading_the_same_name_reports_a_replacement(kb):
    kb.upload("runbook.md", "first revision of the reboot procedure")
    result = kb.upload("runbook.md", "second revision of the reboot procedure")

    assert result["replaced"] is True
    assert kb.sources() == {"noc/runbook.md"}


@pytest.mark.parametrize(
    "filename, content",
    [
        ("notes.md", "# Heading\n\nsome body text about chargers"),
        ("notes.txt", "plain text about chargers"),
        ("rows.csv", "id,fault\n1,comms loss\n2,over temperature\n"),
        ("rows.tsv", "id\tfault\n1\tcomms loss\n"),
        ("data.json", '{"fault": "comms loss", "action": "power cycle the charger"}'),
        ("data.xml", "<faults><fault>comms loss on the charge point</fault></faults>"),
        ("page.html", "<html><body><p>comms loss on the charge point</p></body></html>"),
    ],
)
def test_every_supported_text_format_still_uploads(kb, filename, content):
    """Guard for the size-limit change: raising a ceiling must not narrow the format
    allowlist that upload and the indexer share."""
    result = kb.upload(filename, content)

    assert result["uploaded"] == filename
    assert f"noc/{filename}" in kb.sources()


def test_an_unsupported_type_is_still_refused(kb):
    with pytest.raises(ManageError, match="unsupported type"):
        kb.upload("payload.exe", "not a document")

    assert not (kb.knowledge_dir / "noc" / "payload.exe").exists()


# ---------------------------------------------------------------------------
# Many files in one operation
# ---------------------------------------------------------------------------


def test_uploading_several_documents_indexes_every_one(kb):
    files = [
        ("ocpp.md", "# OCPP\n\nBootNotification is sent on connect."),
        ("sla.txt", "Response time for a down charger is four hours."),
        ("faults.csv", "code,meaning\n42,comms loss\n17,over temperature\n"),
    ]

    for name, content in files:
        assert kb.upload(name, content)["uploaded"] == name

    assert kb.sources() == {"noc/ocpp.md", "noc/sla.txt", "noc/faults.csv"}


def test_a_batch_of_text_and_binary_files_all_reach_the_index(kb):
    kb.upload("ocpp.md", "# OCPP\n\nBootNotification is sent on connect.")
    kb.upload("manual.pdf", content_b64=base64.b64encode(_pdf("Charger service manual.")).decode())
    kb.upload("sla.txt", "Response time for a down charger is four hours.")

    assert kb.sources() == {"noc/ocpp.md", "noc/manual.pdf", "noc/sla.txt"}


def test_one_unreadable_file_does_not_prevent_the_others(kb):
    """The isolation bulk upload depends on: a file that cannot be parsed is refused
    and rolled back, and the files around it are indexed anyway."""
    kb.upload("good-before.md", "# Before\n\nthe first good document")

    with pytest.raises(ManageError, match="could not be parsed"):
        kb.upload("broken.pdf", content_b64=base64.b64encode(b"this is not a PDF at all").decode())

    kb.upload("good-after.md", "# After\n\nthe second good document")

    assert kb.sources() == {"noc/good-before.md", "noc/good-after.md"}
    # Rolled back, not left on disk as a permanently unretrievable document.
    assert not (kb.knowledge_dir / "noc" / "broken.pdf").exists()


def test_a_refused_file_does_not_disturb_an_existing_document_of_the_same_name(kb):
    kb.upload("manual.pdf", content_b64=base64.b64encode(_pdf("The original manual.")).decode())

    with pytest.raises(ManageError, match="could not be parsed"):
        kb.upload("manual.pdf", content_b64=base64.b64encode(b"corrupt").decode())

    restored = (kb.knowledge_dir / "noc" / "manual.pdf").read_bytes()
    assert restored.startswith(b"%PDF")
    assert "noc/manual.pdf" in kb.sources()


def test_a_batch_uploaded_into_one_kb_never_lands_in_another(kb):
    """Isolation is per knowledge base, so a whole batch must stay inside its own
    collection even though `upload_document` re-indexes the entire corpus."""
    kb.upload("ocpp.md", "# OCPP\n\nBootNotification is sent on connect.", profile="noc")
    kb.upload("faults.csv", "code,meaning\n42,comms loss\n", profile="noc")
    kb.upload("budget.md", "# Budget\n\nQ3 capex is approved.", profile="finance")

    assert kb.sources("noc") == {"noc/ocpp.md", "noc/faults.csv"}
    assert kb.sources("finance") == {"finance/budget.md"}
    # Separate collections, so no payload-filter bug can cross them.
    assert kb.store_registry.for_scope("noc").collection == "kb_noc"
    assert kb.store_registry.for_scope("finance").collection == "kb_finance"


# ---------------------------------------------------------------------------
# Size ceilings
# ---------------------------------------------------------------------------


def test_the_upload_ceiling_is_one_hundred_megabytes():
    """The value the rest of the upload path is aligned to. Changing it here without
    changing nginx, the express body parser, the API route and the client is what
    test_upload_limits.py catches."""
    assert manage.MAX_UPLOAD_BYTES == 100 * 1024 * 1024
    assert manage.MAX_UPLOAD_CHARS == 100_000_000


def test_a_binary_file_of_exactly_the_ceiling_is_indexed(kb, monkeypatch):
    payload = _pdf("A document that sits exactly on the ceiling.")
    monkeypatch.setattr(manage, "MAX_UPLOAD_BYTES", len(payload))

    result = kb.upload("at-limit.pdf", content_b64=base64.b64encode(payload).decode())

    assert result["bytes"] == len(payload)
    assert "noc/at-limit.pdf" in kb.sources()


def test_a_binary_file_one_byte_over_the_ceiling_is_refused(kb, monkeypatch):
    payload = _pdf("A document one byte too large.")
    monkeypatch.setattr(manage, "MAX_UPLOAD_BYTES", len(payload) - 1)

    with pytest.raises(ManageError, match="exceeds"):
        kb.upload("over-limit.pdf", content_b64=base64.b64encode(payload).decode())

    assert not (kb.knowledge_dir / "noc" / "over-limit.pdf").exists()


def test_a_file_of_exactly_the_real_ceiling_passes_the_size_gate(kb):
    """The real ceiling, not a monkeypatched one -- 100 MB of it, by the assertion
    above. 100 MB of zeroes is not a readable PDF, so the upload still fails, but it
    must fail at the PARSER: that is what proves the size gate let a file of exactly
    the limit through rather than refusing it."""
    content_b64 = base64.b64encode(b"\0" * manage.MAX_UPLOAD_BYTES).decode()

    with pytest.raises(ManageError) as refusal:
        kb.upload("at-real-limit.pdf", content_b64=content_b64)

    assert "could not be parsed" in str(refusal.value)
    assert "exceeds" not in str(refusal.value)


def test_a_file_over_the_real_ceiling_is_refused_before_it_is_decoded(kb):
    """A caller that sends a gigabyte of base64 must be refused on the encoded length,
    without first materialising the decoded bytes to measure them.

    The payload is deliberately NOT valid base64: if the size check ran after the
    decode, the failure would be "not valid base64" instead of the size refusal.

    Over by a kilobyte rather than by one byte, because base64 pads to a multiple of 4
    characters -- a file one byte over the ceiling encodes to exactly the same number of
    characters as one exactly on it, so the encoded length cannot tell them apart. That
    is what the exact check on the decoded length is still there for, covered by
    test_a_binary_file_one_byte_over_the_ceiling_is_refused.
    """
    clearly_over = _b64_of_size(manage.MAX_UPLOAD_BYTES + 1024)
    not_base64 = "!" + clearly_over[1:]

    with pytest.raises(ManageError, match="exceeds 100 MB"):
        kb.upload("too-big.pdf", content_b64=not_base64)

    assert not (kb.knowledge_dir / "noc" / "too-big.pdf").exists()


def test_a_text_file_of_exactly_the_character_ceiling_is_accepted(kb, monkeypatch):
    content = "Charger fault notes.\n\nComms loss on the charge point.\n"
    monkeypatch.setattr(manage, "MAX_UPLOAD_CHARS", len(content))

    result = kb.upload("notes.md", content)

    assert result["characters"] == len(content)
    assert "noc/notes.md" in kb.sources()


def test_a_text_file_over_the_character_ceiling_is_refused(kb):
    with pytest.raises(ManageError, match="exceeds 100000000 characters"):
        kb.upload("huge.md", "x" * (manage.MAX_UPLOAD_CHARS + 1))

    assert not (kb.knowledge_dir / "noc" / "huge.md").exists()
