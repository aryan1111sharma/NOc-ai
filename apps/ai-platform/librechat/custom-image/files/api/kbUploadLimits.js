/**
 * Upload size ceilings for the Knowledge Base API, and the check that applies them.
 *
 * Split out of knowledgeBases.js for two reasons:
 *
 *   * knowledgeBases.js cannot be loaded outside a built LibreChat image (it requires
 *     `~/server/middleware`, `@librechat/data-schemas`, ...), so the arithmetic that
 *     decides whether a 100 MB PDF is accepted had no way to be tested. This module
 *     has no dependencies at all -- see custom-image/tests/kbUploadLimits.test.js.
 *   * There is now exactly one place on the API side that states the limit, so the
 *     route cannot disagree with itself about what it enforces.
 *
 * THE LIMITS ON THE UPLOAD PATH, loosest last:
 *
 *   client/kbUpload.ts sizeError()            100 MB decoded  - refuses before reading
 *   this module                                100 MB decoded  - refuses with JSON
 *   express.json for /api/knowledge-bases      150 mb of JSON  (see apply-patches.sh)
 *   nginx client_max_body_size                 160 m           (see nginx.conf)
 *   kb-mcp manage.MAX_UPLOAD_BYTES             100 MB decoded  - the backstop
 *
 * The ordering is the point, not the individual numbers. A binary upload travels
 * base64-encoded inside a JSON body and base64 inflates by 4/3, so 100 MB of file is
 * ~133.4 MB on the wire; both transport limits are set above that. Keeping the
 * transport looser than the application means the application is always what refuses
 * an oversized file, and the operator gets `{"message": "File too large: ..."}`
 * rather than nginx's HTML error page or a bare body-parser 413 with no reason the
 * Knowledge Bases UI can display.
 *
 * services/mcp/kb/tests/test_upload_limits.py asserts that ordering across all five
 * files, so raising one of them alone fails the test suite instead of producing the
 * mismatch this replaced (the API capped PDFs at 2 MB while kb-mcp accepted 25 MB).
 */

/** Ceiling for binary uploads, in DECODED bytes. */
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

/** Ceiling for text uploads, in characters -- which is what kb-mcp enforces
 *  (`manage.MAX_UPLOAD_CHARS`). Practically the same size as the byte ceiling for
 *  the ASCII-dominated documents in a NOC corpus. */
const MAX_UPLOAD_CHARS = 100_000_000;

/**
 * How many bytes a base64 string decodes to.
 *
 * Padding is subtracted rather than ignored: `length * 3 / 4` over-reports by up to
 * two bytes, which is enough to reject a file of exactly the limit.
 */
function decodedBytes(contentB64) {
  const length = contentB64.length;
  if (length === 0) {
    return 0;
  }
  let padding = 0;
  if (contentB64.charCodeAt(length - 1) === 0x3d) {
    padding = contentB64.charCodeAt(length - 2) === 0x3d ? 2 : 1;
  }
  return Math.floor(length / 4) * 3 - padding;
}

/**
 * Refuse an oversized upload body, or return null to let it through.
 *
 * Text and binary are measured against different ceilings because kb-mcp enforces
 * different ones. For base64 the DECODED size is what counts: the wire form is ~33%
 * larger, and checking the encoded length would silently lower the real limit by a
 * quarter.
 *
 * Returns `{ status, message }` so the caller decides how to respond; this module
 * stays free of express so it can be unit-tested.
 */
function checkUploadSize({ content, content_b64: contentB64 } = {}) {
  if (typeof contentB64 === 'string' && contentB64.length > 0) {
    const bytes = decodedBytes(contentB64);
    if (bytes > MAX_UPLOAD_BYTES) {
      return {
        status: 413,
        message:
          `File too large: ${(bytes / (1024 * 1024)).toFixed(1)} MB exceeds the ` +
          `${MAX_UPLOAD_BYTES / (1024 * 1024)} MB limit for binary uploads`,
      };
    }
    return null;
  }

  if (typeof content === 'string' && content.length > MAX_UPLOAD_CHARS) {
    return {
      status: 413,
      message:
        `File too large: ${content.length.toLocaleString('en-US')} characters exceeds ` +
        `the ${MAX_UPLOAD_CHARS.toLocaleString('en-US')} character limit for text uploads`,
    };
  }

  return null;
}

module.exports = { MAX_UPLOAD_BYTES, MAX_UPLOAD_CHARS, decodedBytes, checkUploadSize };
