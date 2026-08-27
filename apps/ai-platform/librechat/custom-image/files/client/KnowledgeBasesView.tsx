/**
 * Knowledge Bases management page (ADMIN only).
 *
 * A management surface over the existing KB API. It holds no KB logic of its own:
 * every action maps 1:1 onto an endpoint that delegates to kb-mcp, so the UI can
 * never disagree with the backend about what a knowledge base is.
 *
 * Styling reuses LibreChat's own tokens (surface-*, text-*, border-*) and the
 * shared @librechat/client primitives rather than introducing a second design
 * system. Server-side authorization is the real boundary; this page being hidden
 * from non-admins is convenience, not security.
 */
import { useCallback, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Database, FileText, Plus, RefreshCw, Trash2, Upload } from 'lucide-react';
import { Button, Spinner } from '@librechat/client';
import { request } from 'librechat-data-provider';

type KnowledgeBase = {
  name: string;
  /** Optional grouping label. Several KBs may share one; a KB may have none. */
  department?: string;
  department_known?: boolean;
  scope?: string;
  description?: string;
  collection?: string;
  indexed?: boolean;
  /** Distinct sources in the Qdrant collection: how much is retrievable. */
  document_count?: number;
  /** Files in the KB folder: how much has been put there. May exceed
   *  document_count when a file is present but not yet indexed. */
  documents_on_disk?: number;
};

type DepartmentOption = { name: string; declared?: boolean; knowledge_bases?: string[] };

type KbDocument = { name: string; bytes: number };

/**
 * All calls go through LibreChat's own `request` helper, NOT bare fetch.
 *
 * That is not a style preference: `requireJwtAuth` authenticates from the
 * `Authorization: Bearer` header only, and the access token lives in memory, not
 * in a cookie. A bare `fetch(..., { credentials: 'include' })` therefore sends no
 * credential the API recognises and gets a flat 401. `request` is the axios
 * instance that AuthContext primes via `setTokenHeader()`, so it carries the
 * token and participates in the existing refresh flow.
 */
const api = {
  async request<T>(path: string, init?: { method?: string; body?: string }): Promise<T> {
    const url = `/api/knowledge-bases${path}`;
    const method = (init?.method ?? 'GET').toUpperCase();
    const body = init?.body != null ? JSON.parse(init.body) : undefined;
    try {
      if (method === 'POST') {
        return (await request.post(url, body ?? {})) as T;
      }
      if (method === 'DELETE') {
        return (await request.delete(url)) as T;
      }
      return (await request.get(url)) as T;
    } catch (e: any) {
      /* Surface the API's own message ("department X already has...") rather than
         axios's generic "Request failed with status code 400", which tells the
         operator nothing about what to do differently. */
      throw new Error(
        e?.response?.data?.message ?? e?.message ?? 'Knowledge base request failed',
      );
    }
  },
};

/** Fail fast and visibly. The default retry schedule leaves a spinner on screen
 *  for seconds before an error ever renders, which reads as a hang. */
const QUERY_OPTS = { retry: 1, refetchOnWindowFocus: false } as const;

function formatBytes(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function Banner({ kind, children }: { kind: 'error' | 'info'; children: React.ReactNode }) {
  const tone =
    kind === 'error'
      ? 'border-red-500/40 bg-red-500/10 text-red-500'
      : 'border-border-medium bg-surface-secondary text-text-secondary';
  return <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${tone}`}>{children}</div>;
}

/* -------------------------------------------------------------------------- */
/* Create dialog                                                              */
/* -------------------------------------------------------------------------- */

function CreateKbDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [department, setDepartment] = useState('');
  const [error, setError] = useState<string | null>(null);

  const { data: departments } = useQuery({
    queryKey: ['kb-departments'],
    queryFn: () => api.request<{ departments: DepartmentOption[] }>('/departments'),
    ...QUERY_OPTS,
  });

  /** Every label already in play, offered as suggestions only. Nothing is
   *  filtered out: a department may hold any number of knowledge bases, and a
   *  brand-new label may be typed straight into the field, so there is no
   *  separate department-creation step. */
  const suggestions = useMemo(
    () =>
      Array.from(new Set((departments?.departments ?? []).map((d) => d.name).filter(Boolean))).sort(),
    [departments],
  );

  const create = useMutation({
    mutationFn: () =>
      api.request('', { method: 'POST', body: JSON.stringify({ name, department }) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kbs'] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl border border-border-light bg-surface-primary p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-text-primary">Create Knowledge Base</h2>
        {error && <Banner kind="error">{error}</Banner>}

        <label className="mb-1 block text-sm font-medium text-text-secondary">Name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. marketing"
          className="mb-4 w-full rounded-lg border border-border-medium bg-surface-secondary px-3 py-2 text-sm text-text-primary outline-none focus:border-border-heavy"
        />

        <label className="mb-1 block text-sm font-medium text-text-secondary">
          Department <span className="font-normal text-text-tertiary">(optional)</span>
        </label>
        <input
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
          list="kb-department-options"
          placeholder="Pick an existing one or type a new one"
          className="mb-1 w-full rounded-lg border border-border-medium bg-surface-secondary px-3 py-2 text-sm text-text-primary outline-none focus:border-border-heavy"
        />
        <datalist id="kb-department-options">
          {suggestions.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <p className="mb-2 text-xs text-text-tertiary">
          A department is just a label for grouping. Several knowledge bases can share
          one, and a new department is created simply by naming it here.
        </p>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!name || create.isLoading}
            onClick={() => {
              setError(null);
              create.mutate();
            }}
          >
            {create.isLoading ? <Spinner className="h-4 w-4" /> : 'Create'}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Detail view                                                                */
/* -------------------------------------------------------------------------- */

function KbDetail({ kb, onBack }: { kb: KnowledgeBase; onBack: () => void }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const docsKey = ['kb-docs', kb.name];
  const { data, isLoading, error: docsError } = useQuery({
    queryKey: docsKey,
    queryFn: () => api.request<{ documents: KbDocument[]; count: number }>(`/${kb.name}/documents`),
    ...QUERY_OPTS,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: docsKey });
    queryClient.invalidateQueries({ queryKey: ['kbs'] });
  };

  /** Formats whose bytes do not survive being read as text. Sent base64 instead. */
  const BINARY_EXT = ['.pdf', '.docx', '.xlsx', '.xlsm', '.xls', '.pptx', '.rtf'];

  /* Mirrors the server ceilings in api/server/routes/knowledgeBases.js. Checked here as
     well as there because the two failure modes differ: the server can only answer once
     it has the body, and a body large enough to overflow LibreChat's own
     express.json({ limit: '3mb' }) never reaches our handler at all - it dies in the body
     parser and surfaces as a bare 500 "An unknown error occurred." Refusing the file
     before it is encoded keeps the reason visible and saves reading a multi-MB file into
     memory only to have it rejected. */
  const MAX_BINARY_BYTES = 2 * 1024 * 1024;
  const MAX_TEXT_CHARS = 1_000_000;

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const isBinary = BINARY_EXT.some((x) => file.name.toLowerCase().endsWith(x));
      if (isBinary && file.size > MAX_BINARY_BYTES) {
        throw new Error(
          `${file.name} is ${(file.size / (1024 * 1024)).toFixed(1)} MB, over the ` +
            `${MAX_BINARY_BYTES / (1024 * 1024)} MB limit for PDF/Office uploads.`,
        );
      }
      let body: string;
      if (isBinary) {
        // Chunked conversion: String.fromCharCode(...bytes) on a multi-MB array blows
        // the argument limit and throws, so walk the buffer in slices.
        const buf = new Uint8Array(await file.arrayBuffer());
        let binary = '';
        for (let i = 0; i < buf.length; i += 0x8000) {
          binary += String.fromCharCode.apply(null, Array.from(buf.subarray(i, i + 0x8000)));
        }
        body = JSON.stringify({ filename: file.name, content_b64: btoa(binary) });
      } else {
        const text = await file.text();
        if (text.length > MAX_TEXT_CHARS) {
          throw new Error(
            `${file.name} is ${text.length.toLocaleString()} characters, over the ` +
              `${MAX_TEXT_CHARS.toLocaleString()} character limit for text uploads.`,
          );
        }
        body = JSON.stringify({ filename: file.name, content: text });
      }
      return api.request(`/${kb.name}/documents`, { method: 'POST', body });
    },
    onSuccess: (r: any) => {
      setNotice(`Uploaded and indexed ${r.uploaded ?? ''}`.trim());
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (filename: string) =>
      api.request(`/${kb.name}/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
    onSuccess: () => {
      setNotice('Document deleted and vectors removed');
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const reindex = useMutation({
    mutationFn: () => api.request(`/${kb.name}/reindex`, { method: 'POST' }),
    onSuccess: () => {
      setNotice('Re-index complete');
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const onPick = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        setError(null);
        setNotice(null);
        upload.mutate(file);
      }
      e.target.value = '';
    },
    [upload],
  );

  const docs = (data?.documents ?? []).filter((d) =>
    d.name.toLowerCase().includes(filter.toLowerCase()),
  );
  const busy = upload.isLoading || reindex.isLoading || remove.isLoading;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <button
        onClick={onBack}
        className="mb-4 flex items-center gap-2 text-sm text-text-secondary hover:text-text-primary"
      >
        <ArrowLeft className="h-4 w-4" /> All knowledge bases
      </button>

      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Knowledge Base: {kb.name}</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Department: {kb.department || '—'} · Documents:{' '}
            {data?.count ?? kb.document_count ?? 0} · Status:{' '}
            {kb.indexed ? 'Ready' : 'Not indexed'}
          </p>
        </div>
        <div className="flex gap-2">
          <label>
            <input
              type="file"
              accept=".pdf,.docx,.xlsx,.xlsm,.xls,.pptx,.rtf,.md,.markdown,.txt,.html,.htm,.csv,.tsv,.json,.xml"
              className="hidden"
              onChange={onPick}
              disabled={busy}
            />
            <span className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-surface-submit px-4 py-2 text-sm font-medium text-white hover:opacity-90">
              <Upload className="h-4 w-4" /> Upload Files
            </span>
          </label>
          <Button variant="outline" disabled={busy} onClick={() => reindex.mutate()}>
            <RefreshCw className={`mr-2 h-4 w-4 ${reindex.isLoading ? 'animate-spin' : ''}`} />
            Re-index
          </Button>
        </div>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {notice && !error && <Banner kind="info">{notice}</Banner>}

      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Search documents…"
        className="mb-4 w-full max-w-sm rounded-lg border border-border-medium bg-surface-secondary px-3 py-2 text-sm text-text-primary outline-none focus:border-border-heavy"
      />

      {isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : docsError ? (
        <Banner kind="error">
          Could not load documents: {(docsError as Error)?.message ?? 'unknown error'}
        </Banner>
      ) : docs.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border-medium py-16 text-center">
          <FileText className="mx-auto mb-3 h-8 w-8 text-text-tertiary" />
          <p className="text-sm text-text-secondary">
            {filter ? 'No documents match your search.' : 'No documents yet.'}
          </p>
          {!filter && (
            <p className="mt-1 text-xs text-text-tertiary">
              Upload a PDF, Word, Excel, PowerPoint, CSV, JSON, XML, RTF, Markdown, text or HTML file to index it.
            </p>
          )}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border-light">
          <table className="w-full text-sm">
            <thead className="bg-surface-secondary text-left text-text-secondary">
              <tr>
                <th className="px-4 py-3 font-medium">Filename</th>
                <th className="px-4 py-3 font-medium">Size</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.name} className="border-t border-border-light">
                  <td className="px-4 py-3 text-text-primary">{d.name}</td>
                  <td className="px-4 py-3 text-text-secondary">{formatBytes(d.bytes)}</td>
                  <td className="px-4 py-3 text-text-secondary">Indexed</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      disabled={busy}
                      onClick={() => {
                        setError(null);
                        setNotice(null);
                        remove.mutate(d.name);
                      }}
                      className="inline-flex items-center gap-1 text-red-500 hover:underline disabled:opacity-50"
                    >
                      <Trash2 className="h-4 w-4" /> Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* List view                                                                  */
/* -------------------------------------------------------------------------- */

export default function KnowledgeBasesView() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    data,
    isLoading,
    isError,
    error: loadError,
    refetch,
  } = useQuery({
    queryKey: ['kbs'],
    queryFn: () => api.request<{ knowledge_bases: KnowledgeBase[] }>(''),
    ...QUERY_OPTS,
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.request(`/${name}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['kbs'] }),
    onError: (e: Error) => setError(e.message),
  });

  if (selected) {
    const fresh =
      (data?.knowledge_bases ?? []).find((k) => k.name === selected.name) ?? selected;
    return <KbDetail kb={fresh} onBack={() => setSelected(null)} />;
  }

  const kbs = data?.knowledge_bases ?? [];

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Knowledge Bases</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Departmental knowledge bases and their indexed documents.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="mr-2 h-4 w-4" /> Create Knowledge Base
        </Button>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {isError && (
        <Banner kind="error">
          <div className="flex items-center justify-between gap-4">
            <span>
              Could not load knowledge bases: {(loadError as Error)?.message ?? 'unknown error'}
            </span>
            <Button variant="outline" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        </Banner>
      )}

      {isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : kbs.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border-medium py-16 text-center">
          <Database className="mx-auto mb-3 h-8 w-8 text-text-tertiary" />
          <p className="text-sm text-text-secondary">No knowledge bases yet.</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {kbs.map((kb) => (
            <div
              key={kb.name}
              className="flex items-center justify-between rounded-xl border border-border-light bg-surface-primary-alt p-4"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-text-secondary" />
                  <span className="font-medium text-text-primary">{kb.name}</span>
                </div>
                {/* Documents = files present on disk; the Qdrant count is only called out
                    when it is lower, because that gap is the actionable state (Re-index)
                    rather than a number worth showing on every row. */}
                <p className="mt-1 text-sm text-text-secondary">
                  Department: {kb.department || '—'} · Documents:{' '}
                  {kb.documents_on_disk ?? kb.document_count ?? 0}
                  {kb.documents_on_disk != null &&
                    kb.document_count != null &&
                    kb.documents_on_disk > kb.document_count && (
                      <span className="text-text-tertiary">
                        {' '}
                        ({kb.documents_on_disk - kb.document_count} not indexed)
                      </span>
                    )}{' '}
                  · Status: {kb.indexed ? 'Ready' : 'Not indexed'}
                </p>
                {kb.scope && (
                  <p className="mt-0.5 text-xs text-text-tertiary">
                    Collection: {kb.collection ?? `kb_${kb.scope}`}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <Button variant="outline" onClick={() => setSelected(kb)}>
                  Manage
                </Button>
                <Button
                  variant="outline"
                  disabled={remove.isLoading}
                  onClick={() => {
                    setError(null);
                    if (
                      window.confirm(
                        `Delete knowledge base "${kb.name}"? Its vector collection is dropped; documents on disk are kept.`,
                      )
                    ) {
                      remove.mutate(kb.name);
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {creating && <CreateKbDialog onClose={() => setCreating(false)} />}
    </div>
  );
}
