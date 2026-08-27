/**
 * Knowledge Base management API.
 *
 * A thin, authenticated HTTP facade over the EXISTING kb-mcp servers. It owns no
 * state: every operation is delegated to a tool that already exists, so this file
 * cannot drift from the KB implementation and cannot become a second source of
 * truth about knowledge bases.
 *
 *   GET    /api/knowledge-bases                       -> kb_admin_list_kbs
 *   GET    /api/knowledge-bases/departments           -> kb_admin_list_departments
 *   POST   /api/knowledge-bases                       -> kb_admin_create_kb
 *   DELETE /api/knowledge-bases/:name                 -> kb_admin_delete_kb
 *   GET    /api/knowledge-bases/:name/documents       -> kb_documents_<name>
 *   POST   /api/knowledge-bases/:name/documents       -> kb_upload_<name>
 *   DELETE /api/knowledge-bases/:name/documents/:file -> kb_delete_<name>
 *   POST   /api/knowledge-bases/:name/reindex         -> kb_reindex_<name>
 *
 * Authorization uses LibreChat's own capability system, not a new one:
 *
 *   access:admin        - held by ADMIN *and* by every delegated department admin,
 *                         so it is necessary but NOT sufficient on its own.
 *   manage:mcpservers   - held only by ADMIN in this deployment. This is the bit
 *                         that actually keeps department heads out.
 *
 * Requiring both means a department admin (access:admin, manage:groups, read:*)
 * gets 403 on every route here, while a Super Admin can later delegate global KB
 * management to any principal by granting manage:mcpservers in Admin Panel ->
 * Access -> Grants. No role name appears anywhere in this file.
 *
 * The kb-mcp endpoints are internal to the Docker network and are never reachable
 * from the browser; no MCP, Qdrant or embedding credentials cross this boundary.
 */
const express = require('express');
const { logger } = require('@librechat/data-schemas');
const { SystemCapabilities } = require('@librechat/data-schemas');
const { requireCapability } = require('~/server/middleware/roles/capabilities');
const { requireJwtAuth } = require('~/server/middleware');
const { reinitMCPServer } = require('~/server/services/Tools/mcp');

const router = express.Router();

/** Internal service URLs. Overridable so a test container can point elsewhere. */
const KB_ADMIN_URL = process.env.KB_ADMIN_MCP_URL || 'http://kb-admin-mcp-server:8109/mcp';
const KB_DEPT_URL = process.env.KB_MCP_URL || 'http://kb-mcp-server:8106/mcp';

/** Upload ceilings, mirroring kb-mcp's two DIFFERENT limits (kb_mcp/manage.py).
 *
 *  kb-mcp caps text at MAX_UPLOAD_CHARS (1,000,000 characters) and binary at
 *  MAX_UPLOAD_BYTES (25 MB) - as its own comment puts it, "the meaningful limit
 *  there is decoded bytes, not characters". This file previously applied the TEXT
 *  character limit to BINARY uploads too, which capped every PDF/DOCX/XLSX at 1 MB
 *  even though the backend would have accepted 25 MB.
 *
 *  The binary ceiling is not 25 MB here because a binary upload travels base64-encoded
 *  inside a JSON body, and LibreChat parses that body with express.json({ limit: '3mb' })
 *  (api/server/index.js). Base64 inflates by 4/3, so 3 MB of JSON carries ~2.25 MB of
 *  file. 2 MB decoded leaves headroom for the JSON envelope and keeps this check - which
 *  returns a readable message - firing before the body parser's bare 413. Raising it
 *  further requires raising that express.json limit first. */
const MAX_UPLOAD_CHARS = 1_000_000;
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;

/** The department-facing MCP server whose tool list changes when a KB is created. */
const MCP_SERVER_NAME = 'kb';

/**
 * Minimal MCP streamable-http client.
 *
 * Deliberately not LibreChat's MCPManager: that manager is the *agent runtime's*
 * connection pool, and borrowing it would couple this admin surface to per-user
 * connection state, OAuth flows and tool caches that have nothing to do with KB
 * administration. A stateless call per request is both simpler and unable to
 * perturb the agent path.
 */
async function callMcpTool(baseUrl, toolName, args = {}) {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
  };

  const rpc = async (body, sessionId) => {
    const res = await fetch(baseUrl, {
      method: 'POST',
      headers: sessionId ? { ...headers, 'mcp-session-id': sessionId } : headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`MCP ${toolName} HTTP ${res.status}`);
    }
    return res;
  };

  const initRes = await rpc({
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'librechat-kb-api', version: '1.0.0' },
    },
  });
  const sessionId = initRes.headers.get('mcp-session-id');
  await initRes.text();

  await fetch(baseUrl, {
    method: 'POST',
    headers: sessionId ? { ...headers, 'mcp-session-id': sessionId } : headers,
    body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
  });

  const callRes = await rpc(
    { jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: toolName, arguments: args } },
    sessionId,
  );
  const raw = await callRes.text();

  /** The endpoint may answer as SSE or plain JSON depending on negotiation; take
   *  the last `data:` frame when framed, otherwise the whole body. */
  let payload = raw;
  if (raw.includes('data:')) {
    const frames = raw
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).trim());
    payload = frames[frames.length - 1] ?? raw;
  }

  const parsed = JSON.parse(payload);
  if (parsed.error) {
    throw new Error(parsed.error.message || `MCP ${toolName} failed`);
  }

  const content = parsed.result?.content?.[0]?.text;
  return content ? JSON.parse(content) : (parsed.result?.structuredContent ?? parsed.result ?? {});
}

/** List tool names from an MCP endpoint. Same handshake as callMcpTool, but
 *  tools/list rather than tools/call. */
async function listMcpToolNames(baseUrl) {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
  };
  const initRes = await fetch(baseUrl, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'librechat-kb-api', version: '1.0.0' } },
    }),
  });
  const sessionId = initRes.headers.get('mcp-session-id');
  await initRes.text();
  const h2 = sessionId ? { ...headers, 'mcp-session-id': sessionId } : headers;
  await fetch(baseUrl, { method: 'POST', headers: h2, body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) });
  const res = await fetch(baseUrl, { method: 'POST', headers: h2, body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list' }) });
  const raw = await res.text();
  let payload = raw;
  if (raw.includes('data:')) {
    const frames = raw.split(String.fromCharCode(10)).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim());
    payload = frames[frames.length - 1] ?? raw;
  }
  return (JSON.parse(payload).result?.tools ?? []).map((t) => t.name);
}

/** kb-mcp reports refusals as `{ error }` with a 200 result. Surface those as 400
 *  so the UI can distinguish "you asked for something invalid" from "we broke". */
function sendToolResult(res, result) {
  if (result && typeof result === 'object' && typeof result.error === 'string') {
    return res.status(400).json({ message: result.error });
  }
  return res.status(200).json(result);
}

/**
 * Refresh the caller's `kb` MCP connection and tool cache.
 *
 * Creating a KB makes kb-mcp register new kb_search_/kb_upload_ tools, but
 * LibreChat caches an MCP server's tool list per user, so the new KB stays
 * invisible to the very admin who just created it until something forces a
 * reconnect. That gap is the difference between "created" and "usable", and
 * making the operator discover it by hand is a defect, not a workflow.
 *
 * Best-effort on purpose: the KB itself is already created and persisted, so a
 * refresh failure must not turn a successful create into an error response. It
 * is logged and the caller's next reconnect picks the tools up anyway.
 */
/**
 * Wait until kb-mcp has actually registered a tool, then give up.
 *
 * The dept plane learns about a new KB by polling the registry, so for a moment
 * after `kb_admin_create_kb` returns, the KB exists but its tools do not. Anything
 * that refreshes LibreChat's cache during that window just re-reads a list that is
 * still missing the KB -- which is why refreshing alone did not fix this. Waiting
 * for the tool to exist is what makes "create returned" mean "KB is usable".
 */
async function waitForKbTool(toolName, { present = true, timeoutMs = 15000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const names = await listMcpToolNames(KB_DEPT_URL);
      if (names.includes(toolName) === present) {
        return true;
      }
    } catch (error) {
      logger.debug('[knowledgeBases] tools/list probe failed: %s', error.message);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function refreshKbTools(req) {
  try {
    await reinitMCPServer({
      user: req.user,
      serverName: MCP_SERVER_NAME,
      forceNew: true,
      returnOnOAuth: true,
    });
    logger.debug('[knowledgeBases] refreshed kb tool cache for user %s', req.user?.id);
  } catch (error) {
    logger.warn('[knowledgeBases] kb tool refresh failed (KB is still created): %s', error.message);
  }
}

function handle(fn) {
  return async (req, res) => {
    try {
      return await fn(req, res);
    } catch (error) {
      logger.error('[knowledgeBases]', error);
      return res.status(502).json({ message: 'Knowledge base service unavailable' });
    }
  };
}

/** `name` becomes an MCP tool-name fragment (kb_documents_<name>), so constrain it
 *  here rather than letting a crafted value select an unrelated tool. */
const SAFE_NAME = /^[A-Za-z0-9_-]+$/;

function validName(req, res, next) {
  if (!SAFE_NAME.test(req.params.name || '')) {
    return res.status(400).json({ message: 'Invalid knowledge base name' });
  }
  return next();
}

/**
 * Both capabilities are required. Order matters only for the error the caller
 * sees: access:admin first keeps a plain USER's 403 identical to every other
 * admin surface, so this route reveals nothing new about what exists.
 */
router.use(
  requireJwtAuth,
  requireCapability(SystemCapabilities.ACCESS_ADMIN),
  requireCapability(SystemCapabilities.MANAGE_MCP_SERVERS),
);

router.get(
  '/departments',
  handle(async (req, res) =>
    sendToolResult(res, await callMcpTool(KB_ADMIN_URL, 'kb_admin_list_departments')),
  ),
);

/**
 * The listing reports TWO counts per knowledge base, because they answer different
 * questions and are allowed to disagree:
 *
 *   document_count     - distinct sources in the Qdrant collection (from kb_admin_list_kbs),
 *                        i.e. how much is actually retrievable.
 *   documents_on_disk  - files in the KB's folder (from kb_documents_<name>), i.e. how much
 *                        has been put there.
 *
 * They diverge whenever a file is present but unindexed - most commonly when it was copied
 * straight onto the /knowledge bind mount instead of uploaded through kb_upload_*, or when
 * an index pass has not run yet. Previously only the Qdrant number was returned and the UI
 * rendered it as "Documents", so such a knowledge base reported 0 documents while its own
 * document list showed the file sitting there. Surfacing both makes "present but not
 * indexed" a visible state that points at Re-index, instead of a silent contradiction.
 *
 * The per-KB lookups are best-effort: a KB whose dept-plane tools have not been registered
 * yet (the plane re-reads the registry on an interval) simply reports no disk count rather
 * than failing the whole listing.
 */
router.get(
  '/',
  handle(async (req, res) => {
    const result = await callMcpTool(KB_ADMIN_URL, 'kb_admin_list_kbs');
    const kbs = result && Array.isArray(result.knowledge_bases) ? result.knowledge_bases : null;
    if (!kbs) {
      return sendToolResult(res, result);
    }

    await Promise.all(
      kbs.map(async (kb) => {
        try {
          const docs = await callMcpTool(KB_DEPT_URL, `kb_documents_${kb.name}`);
          if (docs && typeof docs.count === 'number') {
            kb.documents_on_disk = docs.count;
          }
        } catch {
          /* best-effort: leave documents_on_disk unset for this KB */
        }
      }),
    );

    return sendToolResult(res, result);
  }),
);

router.post(
  '/',
  handle(async (req, res) => {
    const { name, department, description } = req.body ?? {};
    if (!name || !department) {
      return res.status(400).json({ message: 'name and department are required' });
    }
    const result = await callMcpTool(KB_ADMIN_URL, 'kb_admin_create_kb', {
      name,
      department,
      description: description || `${name} knowledge base.`,
    });
    if (!result?.error) {
      const ready = await waitForKbTool(`kb_search_${name}`, { present: true });
      if (!ready) {
        logger.warn('[knowledgeBases] kb-mcp did not register tools for %s in time', name);
      }
      await refreshKbTools(req);
      result.tools_ready = ready;
    }
    return sendToolResult(res, result);
  }),
);

router.delete(
  '/:name',
  validName,
  handle(async (req, res) => {
    const result = await callMcpTool(KB_ADMIN_URL, 'kb_admin_delete_kb', {
      name: req.params.name,
      /** Default to the non-destructive semantics kb-mcp already defines: the
       *  record and vectors go, documents on disk stay unless explicitly purged. */
      purge_documents: req.query.purge === 'true',
    });
    if (!result?.error) {
      const gone = await waitForKbTool(`kb_search_${req.params.name}`, { present: false });
      if (!gone) {
        logger.warn('[knowledgeBases] kb-mcp did not deregister tools for %s in time', req.params.name);
      }
      await refreshKbTools(req);
      result.tools_removed = gone;
    }
    return sendToolResult(res, result);
  }),
);

router.get(
  '/:name/documents',
  validName,
  handle(async (req, res) =>
    sendToolResult(res, await callMcpTool(KB_DEPT_URL, `kb_documents_${req.params.name}`)),
  ),
);

router.post(
  '/:name/documents',
  validName,
  handle(async (req, res) => {
    const { filename, content, content_b64: contentB64 } = req.body ?? {};
    // Binary formats (.pdf .docx .xlsx .xls .pptx .rtf) cannot survive a JSON string
    // field intact, so the client base64-encodes them into `content_b64`. Text formats
    // keep using `content`. Exactly one must be present.
    const hasText = typeof content === 'string' && content.length > 0;
    const hasBinary = typeof contentB64 === 'string' && contentB64.length > 0;
    if (!filename || (!hasText && !hasBinary)) {
      return res
        .status(400)
        .json({ message: 'filename and one of content / content_b64 are required' });
    }
    // Text and binary are measured against different ceilings because kb-mcp enforces
    // different ones (see MAX_UPLOAD_CHARS / MAX_UPLOAD_BYTES above). For base64 the
    // decoded size is what counts: the wire form is ~33% larger, and rejecting on the
    // encoded length would silently lower the real limit by a quarter.
    if (hasBinary) {
      const bytes = Math.floor((contentB64.length * 3) / 4);
      if (bytes > MAX_UPLOAD_BYTES) {
        return res.status(413).json({
          message: `File too large: ${Math.round(bytes / 1024)} KB exceeds the ${
            MAX_UPLOAD_BYTES / (1024 * 1024)
          } MB limit for binary uploads`,
        });
      }
    } else if (content.length > MAX_UPLOAD_CHARS) {
      return res.status(413).json({
        message: `File too large: ${content.length} characters exceeds the ${MAX_UPLOAD_CHARS} character limit for text uploads`,
      });
    }
    const result = await callMcpTool(KB_DEPT_URL, `kb_upload_${req.params.name}`, {
      filename,
      content: hasText ? content : '',
      content_b64: hasBinary ? contentB64 : '',
    });
    return sendToolResult(res, result);
  }),
);

router.delete(
  '/:name/documents/:filename',
  validName,
  handle(async (req, res) => {
    const result = await callMcpTool(KB_DEPT_URL, `kb_delete_${req.params.name}`, {
      filename: req.params.filename,
    });
    return sendToolResult(res, result);
  }),
);

router.post(
  '/:name/reindex',
  validName,
  handle(async (req, res) =>
    sendToolResult(res, await callMcpTool(KB_DEPT_URL, `kb_reindex_${req.params.name}`)),
  ),
);

module.exports = router;
