/**
 * Knowledge Base upload queue: turn a multi-file selection into a sequence of
 * single-file uploads against the EXISTING upload endpoint.
 *
 * Bulk upload deliberately adds no second ingestion path. Each selected file is
 * POSTed on its own to `POST /api/knowledge-bases/:name/documents`, which is the
 * same endpoint the single-file upload has always used and which delegates to
 * `kb_upload_<name>` -> `upload_document()` -> `ingest()`. "Bulk" is therefore a
 * property of the UI's queue, not of the pipeline: a batch of one is byte-for-byte
 * the request the old single-file button made.
 *
 * WHY ONE REQUEST PER FILE, not one request carrying every file:
 *
 *   * The body ceiling is per request. Keeping one file per request keeps
 *     MAX_UPLOAD_BYTES a *per-file* limit; a combined body would divide it by the
 *     number of files selected, so a 40-file batch would silently cap each file at
 *     2.5 MB.
 *   * Per-file status is observable. A single combined request can only report
 *     "in flight" for the whole batch, then all results at once.
 *   * A failure is already isolated. One file's 400 cannot abort the others,
 *     because the others are separate requests.
 *
 * WHY STRICTLY SEQUENTIAL, not concurrent:
 *
 *   `upload_document()` finishes by running a whole-corpus `ingest(prune=True)`.
 *   Two of those in flight at once would both walk the corpus and both prune, so
 *   they can race over the same collection. Sequential upload is what keeps the
 *   existing pipeline's assumption (one indexer at a time) true.
 *
 * This module holds no React and no HTTP client, so it is unit-testable with the
 * plain Node test runner (see custom-image/tests/kbUpload.test.ts) rather than only
 * inside a built LibreChat image.
 */

/** Formats whose bytes do not survive being read as text. Sent base64 instead.
 *  Mirrors `parse.BINARY` in kb-mcp. */
export const BINARY_EXT = ['.pdf', '.docx', '.xlsx', '.xlsm', '.xls', '.pptx', '.rtf'];

/**
 * Per-file upload ceiling, in decoded bytes. 100 MB.
 *
 * This is one of five limits on the upload path, and they are ordered so that the
 * component which can produce a READABLE error is always the one that refuses first:
 *
 *   this check (100 MB decoded)               - refuses before the file is even read
 *   api/server/routes/kbUploadLimits.js       - same 100 MB, refuses with JSON
 *   express.json for /api/knowledge-bases     - 150 mb of JSON (~133 MB of base64)
 *   nginx client_max_body_size                - 160 m, deliberately the loosest
 *   kb-mcp manage.MAX_UPLOAD_BYTES            - same 100 MB, the backstop
 *
 * A body large enough to overflow the express parser never reaches our handler at
 * all -- it dies in the body parser and surfaces as a bare error with no stated
 * reason -- so the two application-level checks are kept strictly below it.
 */
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

/** Character ceiling for text formats, mirroring kb-mcp's `MAX_UPLOAD_CHARS`.
 *  Text is measured in characters rather than bytes because that is what kb-mcp
 *  enforces; 100,000,000 characters is the same practical size as the byte ceiling
 *  for the ASCII-dominated documents in a NOC corpus. */
export const MAX_TEXT_CHARS = 100_000_000;

export type UploadStatus = 'pending' | 'uploading' | 'indexing' | 'success' | 'failed';

/** One row of the progress table. Carries no `File`, so it is cheap to copy into
 *  React state on every transition. */
export type UploadItem = {
  name: string;
  status: UploadStatus;
  /** Success detail or the failure reason, whichever applies. */
  message?: string;
};

/** The request body the upload endpoint expects: exactly one of the two payload
 *  fields, matching the `content` / `content_b64` split kb_upload_<name> defines. */
export type UploadPayload = {
  filename: string;
  content?: string;
  content_b64?: string;
};

/**
 * Performs one upload.
 *
 * `onBodySent` is invoked once the request body has finished transferring, which is
 * what lets the queue distinguish "uploading" from "indexing": everything after the
 * body is sent is kb-mcp parsing, chunking, embedding and upserting. Callers that
 * cannot observe that boundary may call it immediately -- the file then simply shows
 * as indexing for the whole round trip, which is still the honest majority of it.
 */
export type SendFn = (payload: UploadPayload, onBodySent: () => void) => Promise<unknown>;

export function isBinary(filename: string): boolean {
  const name = filename.toLowerCase();
  return BINARY_EXT.some((ext) => name.endsWith(ext));
}

function formatMb(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Refuse an oversized file before reading it.
 *
 * Reads only `name` and `size`, so a 100 MB file is rejected without being pulled
 * into memory, and the operator gets a reason instead of a stalled tab.
 */
export function sizeError(file: File): string | null {
  if (file.size > MAX_UPLOAD_BYTES) {
    return (
      `${file.name} is ${formatMb(file.size)}, over the ` +
      `${MAX_UPLOAD_BYTES / (1024 * 1024)} MB per-file upload limit.`
    );
  }
  return null;
}

const B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

/**
 * Base64-encode bytes without building a giant intermediate string.
 *
 * `btoa(String.fromCharCode(...bytes))` is the usual idiom and cannot be used here:
 * the spread blows the argument limit on a multi-MB array, and the chunked
 * String.fromCharCode variant still materialises a second copy of the whole file as
 * a UTF-16 string -- 200 MB of it for a 100 MB upload, on top of the buffer and the
 * encoded result. Encoding straight from the Uint8Array keeps peak memory to the
 * file plus its base64 form.
 */
export function toBase64(bytes: Uint8Array): string {
  const parts: string[] = [];
  const whole = bytes.length - (bytes.length % 3);

  // 3 bytes -> 4 characters, so a chunk boundary on a multiple of 3 needs no padding
  // and the pieces concatenate into valid base64.
  const CHUNK = 3 * 16384;
  for (let start = 0; start < whole; start += CHUNK) {
    const end = Math.min(start + CHUNK, whole);
    let out = '';
    for (let i = start; i < end; i += 3) {
      const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
      out +=
        B64_ALPHABET[(n >> 18) & 63] +
        B64_ALPHABET[(n >> 12) & 63] +
        B64_ALPHABET[(n >> 6) & 63] +
        B64_ALPHABET[n & 63];
    }
    parts.push(out);
  }

  // The 1 or 2 trailing bytes, which are the only ones that carry '=' padding.
  const rest = bytes.length - whole;
  if (rest === 1) {
    const n = bytes[whole] << 16;
    parts.push(`${B64_ALPHABET[(n >> 18) & 63]}${B64_ALPHABET[(n >> 12) & 63]}==`);
  } else if (rest === 2) {
    const n = (bytes[whole] << 16) | (bytes[whole + 1] << 8);
    parts.push(
      `${B64_ALPHABET[(n >> 18) & 63]}${B64_ALPHABET[(n >> 12) & 63]}` +
        `${B64_ALPHABET[(n >> 6) & 63]}=`,
    );
  }

  return parts.join('');
}

/** Raised for a file this client refuses outright, so `uploadBatch` can report the
 *  reason on that row and carry on with the next file. */
export class UploadRejected extends Error {}

/**
 * Turn one selected file into the request body for the upload endpoint.
 *
 * Binary formats go to `content_b64`; text formats go to `content` verbatim. This is
 * the same split the endpoint and `kb_upload_<name>` already require -- it is not a
 * bulk-specific encoding.
 */
export async function encodeUpload(file: File): Promise<UploadPayload> {
  if (isBinary(file.name)) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    return { filename: file.name, content_b64: toBase64(bytes) };
  }

  const text = await file.text();
  if (text.length > MAX_TEXT_CHARS) {
    throw new UploadRejected(
      `${file.name} is ${text.length.toLocaleString()} characters, over the ` +
        `${MAX_TEXT_CHARS.toLocaleString()} character limit for text uploads.`,
    );
  }
  return { filename: file.name, content: text };
}

export type BatchResult = {
  items: UploadItem[];
  succeeded: number;
  failed: number;
};

function messageOf(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return 'Upload failed';
}

/**
 * Upload every file in `files`, one after another, reporting each row's status.
 *
 * A file that is refused or that fails server-side is marked `failed` and the queue
 * moves to the next one: the batch is a sequence of independent uploads, so one bad
 * PDF cannot cost the operator the other thirty-nine.
 *
 * `onChange` is called with a fresh array on every transition, which is what lets a
 * React caller pass it straight to a state setter.
 */
export async function uploadBatch(
  files: File[],
  { send, onChange }: { send: SendFn; onChange?: (items: UploadItem[]) => void },
): Promise<BatchResult> {
  const items: UploadItem[] = files.map((file) => ({ name: file.name, status: 'pending' }));

  const emit = () => onChange?.(items.map((item) => ({ ...item })));
  const set = (index: number, status: UploadStatus, message?: string) => {
    items[index] = { name: items[index].name, status, message };
    emit();
  };

  emit();

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    set(i, 'uploading');

    try {
      const refusal = sizeError(file);
      if (refusal) {
        throw new UploadRejected(refusal);
      }

      const payload = await encodeUpload(file);
      const result = (await send(payload, () => set(i, 'indexing'))) as
        | { uploaded?: string; replaced?: boolean }
        | undefined;

      set(i, 'success', result?.replaced ? 'Replaced and indexed' : 'Indexed');
    } catch (error) {
      set(i, 'failed', messageOf(error));
    }
  }

  return {
    items: items.map((item) => ({ ...item })),
    succeeded: items.filter((item) => item.status === 'success').length,
    failed: items.filter((item) => item.status === 'failed').length,
  };
}
