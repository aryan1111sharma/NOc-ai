"""The upload size limit has five definitions; this pins them to one number.

A document upload crosses nginx, LibreChat's express body parser, the Knowledge Bases
API route, the browser page and finally kb-mcp. Each of those has its own ceiling, and
they are not interchangeable: base64 inflates a binary file by ~4/3 on the wire, so the
transport limits must sit ABOVE the application ones, and the application ones must
agree with each other or the tightest wins silently.

That is not hypothetical. Before this test existed the API route capped every PDF at
2 MB while kb-mcp accepted 25 MB and nginx allowed 4 MB, so the documented limit was
wrong in three different directions at once and the symptom was an opaque upload
failure. Reading the real files is the only way to catch that: nginx.conf and
apply-patches.sh are not importable, and nothing else in the suite ever looks at them.

Skipped rather than failed when the LibreChat app tree is absent, so the kb-mcp suite
still runs against a checkout of the service directory alone.
"""
import math
import re
from pathlib import Path

import pytest

from kb_mcp import manage

REPO_ROOT = Path(__file__).resolve().parents[4]
LIBRECHAT = REPO_ROOT / "apps" / "ai-platform" / "librechat"

pytestmark = pytest.mark.skipif(
    not LIBRECHAT.is_dir(),
    reason=f"LibreChat app tree not present at {LIBRECHAT}; nothing to cross-check",
)

API_LIMITS = LIBRECHAT / "custom-image" / "files" / "api" / "kbUploadLimits.js"
CLIENT_QUEUE = LIBRECHAT / "custom-image" / "files" / "client" / "kbUpload.ts"
CLIENT_VIEW = LIBRECHAT / "custom-image" / "files" / "client" / "KnowledgeBasesView.tsx"
APPLY_PATCHES = LIBRECHAT / "custom-image" / "apply-patches.sh"
NGINX_CONF = LIBRECHAT / "nginx.conf"

#: How large `MAX_UPLOAD_BYTES` of file becomes once base64-encoded into a JSON body.
#: Every limit on the transport has to clear this, not the file size.
WIRE_BYTES = math.ceil(manage.MAX_UPLOAD_BYTES / 3) * 4


def _int_expression(text: str) -> int:
    """Evaluate a JS/TS integer literal written as `100 * 1024 * 1024` or `100_000_000`.

    Deliberately not `eval`: only digits, underscores and `*` are accepted, so a
    malformed constant raises instead of running.
    """
    cleaned = text.replace("_", "").strip()
    if not re.fullmatch(r"\d+(\s*\*\s*\d+)*", cleaned):
        raise AssertionError(f"not a plain integer expression: {text!r}")
    return math.prod(int(part) for part in cleaned.split("*"))


def _constant(path: Path, name: str) -> int:
    """The value assigned to `name` in a JS/TS source file, asserted to appear once."""
    source = path.read_text(encoding="utf-8")
    matches = re.findall(rf"^(?:export\s+)?const\s+{name}\s*=\s*([^;]+);", source, re.MULTILINE)
    assert len(matches) == 1, f"expected exactly one `{name}` in {path.name}, found {len(matches)}"
    return _int_expression(matches[0])


def _size_suffix(value: str) -> int:
    """`'150mb'` (express) / `'160m'` (nginx) -> bytes. Both mean mebibytes."""
    match = re.fullmatch(r"(\d+)\s*mb?", value.strip().lower())
    assert match, f"unrecognised size: {value!r}"
    return int(match.group(1)) * 1024 * 1024


def _express_kb_body_limit() -> int:
    """The body limit apply-patches.sh mounts for /api/knowledge-bases.

    Scoped to that path on purpose: LibreChat's global express.json limit stays where
    upstream put it, so raising the KB ceiling does not let every other route accept a
    hundred-megabyte body.
    """
    source = APPLY_PATCHES.read_text(encoding="utf-8")
    matches = re.findall(
        r"app\.use\(\s*'/api/knowledge-bases'\s*,\s*express\.json\(\{\s*limit:\s*'([^']+)'",
        source,
    )
    assert len(matches) == 1, (
        "expected exactly one path-scoped express.json limit for /api/knowledge-bases "
        f"in apply-patches.sh, found {len(matches)}"
    )
    return _size_suffix(matches[0])


def _nginx_body_limit() -> int:
    source = NGINX_CONF.read_text(encoding="utf-8")
    matches = re.findall(r"^\s*client_max_body_size\s+(\S+?);", source, re.MULTILINE)
    assert len(matches) == 1, f"expected one client_max_body_size, found {len(matches)}"
    return _size_suffix(matches[0])


def test_the_api_route_and_kb_mcp_enforce_the_same_byte_ceiling():
    assert _constant(API_LIMITS, "MAX_UPLOAD_BYTES") == manage.MAX_UPLOAD_BYTES


def test_the_browser_page_and_kb_mcp_enforce_the_same_byte_ceiling():
    assert _constant(CLIENT_QUEUE, "MAX_UPLOAD_BYTES") == manage.MAX_UPLOAD_BYTES


def test_every_layer_enforces_the_same_character_ceiling_for_text():
    assert _constant(API_LIMITS, "MAX_UPLOAD_CHARS") == manage.MAX_UPLOAD_CHARS
    assert _constant(CLIENT_QUEUE, "MAX_TEXT_CHARS") == manage.MAX_UPLOAD_CHARS


def test_the_express_body_parser_admits_a_file_of_the_full_ceiling():
    """Base64 makes a 100 MB file ~133 MB of JSON. A parser sized for the FILE would
    reject a file well inside the limit, and body-parser's 413 carries no message the
    Knowledge Bases UI can show."""
    assert _express_kb_body_limit() > WIRE_BYTES


def test_nginx_is_looser_than_the_express_body_parser():
    """Keeping the proxy above the app means the app is always what refuses an
    oversized upload, so the client gets JSON `{"message": ...}` instead of nginx's
    HTML error page."""
    assert _nginx_body_limit() > _express_kb_body_limit()


def test_the_application_ceiling_is_below_both_transport_ceilings():
    """The whole ordering in one assertion: the readable refusals fire first."""
    assert manage.MAX_UPLOAD_BYTES < WIRE_BYTES < _express_kb_body_limit() < _nginx_body_limit()


def test_the_upload_control_accepts_a_multi_file_selection():
    """Bulk upload is only reachable if the file input actually allows it; losing the
    `multiple` attribute would silently return the page to one file at a time."""
    source = CLIENT_VIEW.read_text(encoding="utf-8")
    inputs = re.findall(r"<input\b[^>]*type=\"file\"[^>]*/>", source, re.DOTALL)
    assert len(inputs) == 1, f"expected one file input in KnowledgeBasesView.tsx, found {len(inputs)}"
    assert "multiple" in inputs[0]


def test_the_page_uploads_through_the_shared_queue_rather_than_its_own_loop():
    """Bulk upload must not become a second implementation. The page is expected to
    delegate to kbUpload.ts, which is the module the batch tests cover."""
    source = CLIENT_VIEW.read_text(encoding="utf-8")
    assert re.search(r"from\s+'\./kbUpload'", source)
    assert "uploadBatch" in source


def test_the_api_route_delegates_its_size_check_to_the_shared_module():
    route = (LIBRECHAT / "custom-image" / "files" / "api" / "knowledgeBases.js").read_text(
        encoding="utf-8"
    )
    assert "require('./kbUploadLimits')" in route
    assert "checkUploadSize" in route
    # The old inline ceilings must be gone, or the route has two answers to one question.
    assert not re.search(r"^const MAX_UPLOAD_(BYTES|CHARS)\s*=", route, re.MULTILINE)


def test_apply_patches_installs_the_shared_modules_into_the_image():
    """A module that is never copied makes the image fail to boot on `require`, which
    the Dockerfile's own assertions would not catch."""
    source = APPLY_PATCHES.read_text(encoding="utf-8")
    assert "/patch-files/api/kbUploadLimits.js" in source
    assert "/patch-files/client/kbUpload.ts" in source
