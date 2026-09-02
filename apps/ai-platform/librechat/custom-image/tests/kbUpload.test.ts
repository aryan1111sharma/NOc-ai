/**
 * Tests for the Knowledge Bases bulk-upload queue (files/client/kbUpload.ts).
 *
 * Run with the host's own Node test runner -- no jest, no LibreChat tree:
 *
 *     node --test apps/ai-platform/librechat/custom-image/tests
 *
 * That is why the batch orchestration lives in kbUpload.ts rather than inside
 * KnowledgeBasesView.tsx: the interesting behaviour (sequential processing,
 * per-file status, one failure not stopping the batch, the size gate) is testable
 * as a plain module, while the component keeps only rendering.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_TEXT_CHARS,
  MAX_UPLOAD_BYTES,
  encodeUpload,
  sizeError,
  toBase64,
  uploadBatch,
  type UploadItem,
  type UploadPayload,
} from '../files/client/kbUpload.ts';

/** A recording `send` that always succeeds. */
function recorder() {
  const sent: UploadPayload[] = [];
  const send = async (payload: UploadPayload, onBodySent: () => void) => {
    sent.push(payload);
    onBodySent();
    return { uploaded: payload.filename };
  };
  return { sent, send };
}

const textFile = (name: string, body = 'hello knowledge base') =>
  new File([body], name, { type: 'text/plain' });

const binaryFile = (name: string, bytes: Uint8Array) =>
  new File([bytes], name, { type: 'application/pdf' });

/** Only `.name` and `.size` are read before the size gate, so an oversized file
 *  can be represented without allocating 100 MB in the test process. */
const stubFile = (name: string, size: number) => ({ name, size }) as File;

test('a binary file is base64-encoded exactly, across chunk boundaries', async () => {
  // 200 KB spans several encoder chunks; a chunk size that is not a multiple of 3
  // would insert padding mid-stream and corrupt everything after the first chunk.
  const bytes = new Uint8Array(200 * 1024);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = (i * 31 + 7) % 256;
  }

  const payload = await encodeUpload(binaryFile('manual.pdf', bytes));

  assert.equal(payload.filename, 'manual.pdf');
  assert.equal(payload.content, undefined);
  assert.equal(payload.content_b64, Buffer.from(bytes).toString('base64'));
});

test('base64 padding is correct for every trailing-byte case', () => {
  // A file length that is not a multiple of 3 leaves 1 or 2 bytes over, and those are
  // the only ones that carry '=' padding. Getting either branch wrong corrupts the tail
  // of every file of that length, which no round-number test would notice.
  for (const length of [0, 1, 2, 3, 4, 5, 6, 7, 100, 101, 102]) {
    const bytes = new Uint8Array(length);
    for (let i = 0; i < length; i++) {
      bytes[i] = (i * 71 + 13) % 256;
    }
    assert.equal(toBase64(bytes), Buffer.from(bytes).toString('base64'), `length ${length}`);
  }
});

test('a text file is sent verbatim rather than base64-encoded', async () => {
  const payload = await encodeUpload(textFile('runbook.md', '# Runbook\n\nreboot it'));

  assert.equal(payload.filename, 'runbook.md');
  assert.equal(payload.content, '# Runbook\n\nreboot it');
  assert.equal(payload.content_b64, undefined);
});

test('every selected file is uploaded in one operation', async () => {
  const { sent, send } = recorder();
  const files = [textFile('a.md'), textFile('b.txt'), textFile('c.csv')];

  const result = await uploadBatch(files, { send });

  assert.deepEqual(sent.map((p) => p.filename), ['a.md', 'b.txt', 'c.csv']);
  assert.equal(result.succeeded, 3);
  assert.equal(result.failed, 0);
  assert.deepEqual(result.items.map((i) => i.status), ['success', 'success', 'success']);
});

test('each file reports uploading, then indexing, then success', async () => {
  const seen: string[] = [];
  const send = async (payload: UploadPayload, onBodySent: () => void) => {
    onBodySent();
    return { uploaded: payload.filename };
  };

  await uploadBatch([textFile('a.md')], {
    send,
    onChange: (items: UploadItem[]) => seen.push(items[0].status),
  });

  assert.deepEqual(seen, ['pending', 'uploading', 'indexing', 'success']);
});

test('a file that fails does not stop the files after it', async () => {
  const sent: string[] = [];
  const send = async (payload: UploadPayload, onBodySent: () => void) => {
    sent.push(payload.filename);
    onBodySent();
    if (payload.filename === 'bad.md') {
      throw new Error('bad.md could not be parsed');
    }
    return { uploaded: payload.filename };
  };

  const result = await uploadBatch(
    [textFile('good1.md'), textFile('bad.md'), textFile('good2.md')],
    { send },
  );

  assert.deepEqual(sent, ['good1.md', 'bad.md', 'good2.md']);
  assert.equal(result.succeeded, 2);
  assert.equal(result.failed, 1);
  assert.deepEqual(result.items.map((i) => i.status), ['success', 'failed', 'success']);
  assert.match(result.items[1].message ?? '', /could not be parsed/);
});

test('a file over the size limit is refused without being sent, and the batch continues', async () => {
  const { sent, send } = recorder();
  const files = [
    textFile('small.md'),
    stubFile('huge.pdf', MAX_UPLOAD_BYTES + 1),
    textFile('also-small.md'),
  ];

  const result = await uploadBatch(files, { send });

  assert.deepEqual(sent.map((p) => p.filename), ['small.md', 'also-small.md']);
  assert.equal(result.items[1].status, 'failed');
  assert.match(result.items[1].message ?? '', /100 MB/);
  assert.equal(result.succeeded, 2);
  assert.equal(result.failed, 1);
});

test('a file of exactly the size limit passes the size gate', () => {
  assert.equal(sizeError(stubFile('at-limit.pdf', MAX_UPLOAD_BYTES)), null);
  assert.equal(sizeError(stubFile('under.pdf', MAX_UPLOAD_BYTES - 1)), null);
});

test('a file one byte over the size limit is rejected by the size gate', () => {
  const message = sizeError(stubFile('over.pdf', MAX_UPLOAD_BYTES + 1));
  assert.match(message ?? '', /over\.pdf/);
  assert.match(message ?? '', /100 MB/);
});

test('a text file over the character limit is rejected', async () => {
  const { sent, send } = recorder();
  // Byte size stays under the byte ceiling, so only the character ceiling can catch it.
  const body = 'x'.repeat(MAX_TEXT_CHARS + 1);

  const result = await uploadBatch([textFile('huge.txt', body)], { send });

  assert.equal(sent.length, 0);
  assert.equal(result.items[0].status, 'failed');
  assert.match(result.items[0].message ?? '', /character/);
});

test('a single-file selection still makes exactly one upload request', async () => {
  const { sent, send } = recorder();

  const result = await uploadBatch([textFile('only.md', 'just one')], { send });

  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0], { filename: 'only.md', content: 'just one' });
  assert.equal(result.succeeded, 1);
  assert.equal(result.failed, 0);
});

test('files are uploaded one at a time, never concurrently', async () => {
  // upload_document runs a whole-corpus ingest() per call, which is not safe to run
  // twice at once -- so the queue must be strictly sequential.
  let inFlight = 0;
  let maxInFlight = 0;
  const send = async (payload: UploadPayload, onBodySent: () => void) => {
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((r) => setTimeout(r, 1));
    onBodySent();
    inFlight--;
    return { uploaded: payload.filename };
  };

  await uploadBatch([textFile('a.md'), textFile('b.md'), textFile('c.md')], { send });

  assert.equal(maxInFlight, 1);
});
