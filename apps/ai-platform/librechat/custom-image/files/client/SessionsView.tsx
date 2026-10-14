/**
 * NOC Session Intelligence view (ADMIN only).
 *
 * Read-only observability page over the standalone `session` service (see
 * services/session/README.md), reached through this repo's thin facade
 * (files/api/sessions.js -> GET /api/sessions, GET /api/sessions/:id).
 *
 * Master-detail layout, same shape as LlmUsageView.tsx beside this file:
 * a filterable session list on the left, and on the right the full
 * User request -> Tool Filter -> Provider/model -> fallback -> tool calls ->
 * result -> feedback timeline for whichever session is selected.
 *
 * Everything here is what the pipeline actually observed -- there is no
 * RAG-specific field because no RAG signal is available separately from a
 * generic tool call (see session/mongo_reader.py's module docstring "Known
 * limitations"); a KB/RAG lookup that ran as an MCP tool call still shows up
 * in "Tool calls" below, just not labeled as retrieval on its own. Per-turn
 * latency and token figures come from whichever source the service itself
 * attached (`tokens.source`): "provider_usage" (exact) or
 * "message_token_count" (LibreChat's own estimate) -- never fabricated when
 * absent.
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, AlertCircle, ThumbsDown, ThumbsUp, Wrench } from 'lucide-react';
import { Spinner } from '@librechat/client';
import { request } from 'librechat-data-provider';

type FeedbackSummary = { thumbs_up: number; thumbs_down: number };

type SessionListEntry = {
  conversation_id: string;
  agent_id: string | null;
  user_id: string | null;
  turn_count: number;
  total_tokens: number | null;
  models: string[];
  tools_used: string[];
  feedback_summary: FeedbackSummary;
  started_at: string | null;
  last_activity: string | null;
  status: string;
};

type ToolUsage = { tool_call_id: string | null; name: string; has_output: boolean };

type TurnTimeline = {
  message_id: string;
  timestamp: string | null;
  query_preview: string;
  response_preview: string;
  requested_model: string | null;
  actual_target: string | null;
  fallback_attempted: string[];
  tool_filter: { applied?: boolean; tools_before?: number; tools_after?: number } | null;
  tools: ToolUsage[];
  tokens: Record<string, number | string> | null;
  latency_ms: number | null;
  status: string;
  feedback: { rating?: string; tag?: string; text?: string } | null;
};

type SessionDetailView = {
  conversation_id: string;
  agent_id: string | null;
  user_id: string | null;
  title: string | null;
  started_at: string | null;
  last_activity: string | null;
  timeline: TurnTimeline[];
};

const api = {
  async request<T>(path: string): Promise<T> {
    try {
      return (await request.get(`/api/sessions${path}`)) as T;
    } catch (e: any) {
      throw new Error(e?.response?.data?.message ?? e?.message ?? 'Session request failed');
    }
  },
};

const QUERY_OPTS = { retry: 1, refetchOnWindowFocus: false } as const;

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === 'error'
      ? 'bg-red-500/15 text-red-600'
      : status === 'unfinished'
        ? 'bg-yellow-500/15 text-yellow-600'
        : 'bg-green-500/15 text-green-600';
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${tone}`}>{status}</span>
  );
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500">
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Filters                                                                     */
/* -------------------------------------------------------------------------- */

type Filters = {
  agent: string;
  model: string;
  dateFrom: string;
  dateTo: string;
  search: string;
};

const EMPTY_FILTERS: Filters = { agent: '', model: '', dateFrom: '', dateTo: '', search: '' };

function FiltersBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (next: Filters) => void;
}) {
  const inputClass =
    'rounded-md border border-border-light bg-surface-primary px-2 py-1 text-sm text-text-primary placeholder:text-text-secondary';
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <input
        className={inputClass}
        placeholder="Filter by agent id"
        value={filters.agent}
        onChange={(e) => onChange({ ...filters, agent: e.target.value })}
      />
      <input
        className={inputClass}
        placeholder="Filter by model/provider"
        value={filters.model}
        onChange={(e) => onChange({ ...filters, model: e.target.value })}
      />
      <input
        className={inputClass}
        type="date"
        value={filters.dateFrom}
        onChange={(e) => onChange({ ...filters, dateFrom: e.target.value })}
      />
      <input
        className={inputClass}
        type="date"
        value={filters.dateTo}
        onChange={(e) => onChange({ ...filters, dateTo: e.target.value })}
      />
      <input
        className={inputClass}
        placeholder="Search session id / title"
        value={filters.search}
        onChange={(e) => onChange({ ...filters, search: e.target.value })}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Session list                                                               */
/* -------------------------------------------------------------------------- */

function SessionTable({
  sessions,
  selected,
  onSelect,
}: {
  sessions: SessionListEntry[];
  selected: string | null;
  onSelect: (conversationId: string) => void;
}) {
  if (sessions.length === 0) {
    return <p className="text-sm text-text-secondary">No sessions observed yet.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-border-light">
      <table className="w-full text-left text-xs">
        <thead className="bg-surface-tertiary text-text-secondary">
          <tr>
            <th className="px-2 py-2">Session</th>
            <th className="px-2 py-2">Agent</th>
            <th className="px-2 py-2">User</th>
            <th className="px-2 py-2">Turns</th>
            <th className="px-2 py-2">Tokens</th>
            <th className="px-2 py-2">Models</th>
            <th className="px-2 py-2">Tools</th>
            <th className="px-2 py-2">Feedback</th>
            <th className="px-2 py-2">Started</th>
            <th className="px-2 py-2">Last activity</th>
            <th className="px-2 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr
              key={s.conversation_id}
              onClick={() => onSelect(s.conversation_id)}
              className={`cursor-pointer border-t border-border-light transition-colors ${
                selected === s.conversation_id
                  ? 'bg-surface-tertiary'
                  : 'bg-surface-primary hover:bg-surface-secondary'
              }`}
            >
              <td className="px-2 py-2 font-mono text-text-primary" title={s.conversation_id}>
                {s.conversation_id.slice(0, 8)}
              </td>
              <td className="px-2 py-2 text-text-primary">{s.agent_id ?? '—'}</td>
              <td className="px-2 py-2 text-text-secondary" title={s.user_id ?? ''}>
                {s.user_id ? s.user_id.slice(0, 8) : '—'}
              </td>
              <td className="px-2 py-2 text-text-primary">{s.turn_count}</td>
              <td className="px-2 py-2 text-text-primary">{s.total_tokens ?? '—'}</td>
              <td className="px-2 py-2 text-text-secondary">
                {s.models.length ? s.models.join(', ') : '—'}
              </td>
              <td className="px-2 py-2 text-text-secondary">
                {s.tools_used.length ? s.tools_used.join(', ') : '—'}
              </td>
              <td className="px-2 py-2 text-text-secondary">
                <span className="inline-flex items-center gap-2">
                  <span className="inline-flex items-center gap-0.5">
                    <ThumbsUp className="h-3 w-3" /> {s.feedback_summary.thumbs_up}
                  </span>
                  <span className="inline-flex items-center gap-0.5">
                    <ThumbsDown className="h-3 w-3" /> {s.feedback_summary.thumbs_down}
                  </span>
                </span>
              </td>
              <td className="px-2 py-2 text-text-secondary">{formatTimestamp(s.started_at)}</td>
              <td className="px-2 py-2 text-text-secondary">
                {formatTimestamp(s.last_activity)}
              </td>
              <td className="px-2 py-2">
                <StatusBadge status={s.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Session detail: execution timeline                                        */
/* -------------------------------------------------------------------------- */

function TokensCell({ tokens }: { tokens: Record<string, number | string> | null }) {
  if (!tokens) return <span className="text-text-secondary">not observed</span>;
  const { source, ...rest } = tokens;
  return (
    <span className="text-text-primary">
      {Object.entries(rest)
        .map(([k, v]) => `${k}: ${v}`)
        .join(', ')}{' '}
      <span className="text-text-secondary">
        ({source === 'provider_usage' ? 'provider-reported' : 'estimated from message length'})
      </span>
    </span>
  );
}

function TurnStep({ turn, index }: { turn: TurnTimeline; index: number }) {
  return (
    <div className="rounded-lg border border-border-light bg-surface-primary p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-text-primary">Turn {index + 1}</span>
        <StatusBadge status={turn.status} />
      </div>

      <div className="mb-2 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
        <div className="rounded-md bg-surface-secondary p-2">
          <div className="mb-1 font-semibold text-text-secondary">1. User request</div>
          <div className="text-text-primary">{turn.query_preview || '(empty)'}</div>
        </div>
        <div className="rounded-md bg-surface-secondary p-2">
          <div className="mb-1 font-semibold text-text-secondary">2. Tool Filter</div>
          {turn.tool_filter ? (
            <div className="text-text-primary">
              {turn.tool_filter.applied
                ? `${turn.tool_filter.tools_before} -> ${turn.tool_filter.tools_after} tools`
                : 'not applied'}
            </div>
          ) : (
            <div className="text-text-secondary">no decision recorded</div>
          )}
        </div>
        <div className="rounded-md bg-surface-secondary p-2">
          <div className="mb-1 font-semibold text-text-secondary">3. Provider / model</div>
          <div className="text-text-primary">
            {turn.actual_target ?? turn.requested_model ?? '—'}
          </div>
          {turn.fallback_attempted.length > 1 && (
            <div className="mt-1 text-text-secondary">
              Fallback chain: {turn.fallback_attempted.join(' -> ')}
            </div>
          )}
        </div>
        <div className="rounded-md bg-surface-secondary p-2">
          <div className="mb-1 font-semibold text-text-secondary">4. Tool calls</div>
          {turn.tools.length ? (
            <ul className="flex flex-col gap-0.5 text-text-primary">
              {turn.tools.map((t, i) => (
                <li key={t.tool_call_id ?? i} className="inline-flex items-center gap-1">
                  <Wrench className="h-3 w-3" /> {t.name}{' '}
                  <span className="text-text-secondary">
                    ({t.has_output ? 'completed with output' : 'no output recorded'})
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-text-secondary">none</div>
          )}
        </div>
        <div className="rounded-md bg-surface-secondary p-2">
          <div className="mb-1 font-semibold text-text-secondary">5. Result</div>
          <div className="text-text-primary">{turn.response_preview || '(empty)'}</div>
          <div className="mt-1 text-text-secondary">
            <TokensCell tokens={turn.tokens} /> ·{' '}
            {turn.latency_ms != null ? `${Math.round(turn.latency_ms)}ms` : 'latency not observed'}
          </div>
        </div>
        <div className="rounded-md bg-surface-secondary p-2">
          <div className="mb-1 font-semibold text-text-secondary">6. Feedback</div>
          {turn.feedback ? (
            <div className="inline-flex items-center gap-1 text-text-primary">
              {turn.feedback.rating === 'thumbsUp' ? (
                <ThumbsUp className="h-3 w-3" />
              ) : turn.feedback.rating === 'thumbsDown' ? (
                <ThumbsDown className="h-3 w-3" />
              ) : null}
              {turn.feedback.tag ?? turn.feedback.rating ?? 'recorded'}
              {turn.feedback.text ? `: ${turn.feedback.text}` : ''}
            </div>
          ) : (
            <div className="text-text-secondary">none</div>
          )}
        </div>
      </div>

      <div className="mt-1 text-[11px] text-text-secondary">{formatTimestamp(turn.timestamp)}</div>
    </div>
  );
}

function SessionDetailPanel({ view }: { view: SessionDetailView }) {
  return (
    <div>
      <div className="mb-3">
        <h2 className="text-lg font-semibold text-text-primary">
          {view.title || view.conversation_id}
        </h2>
        <p className="mt-1 text-xs text-text-secondary">
          Agent: {view.agent_id ?? '—'} · User: {view.user_id ?? '—'} · Started{' '}
          {formatTimestamp(view.started_at)} · Last activity {formatTimestamp(view.last_activity)}
        </p>
      </div>
      <div className="flex flex-col gap-3">
        {view.timeline.map((t, i) => (
          <TurnStep key={t.message_id || i} turn={t} index={i} />
        ))}
        {view.timeline.length === 0 && (
          <p className="text-sm text-text-secondary">No turns recorded for this session.</p>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

export default function SessionsView() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ['sessions-list', filters.agent, filters.model, filters.dateFrom, filters.dateTo],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.agent) params.set('agent', filters.agent);
      if (filters.model) params.set('model', filters.model);
      if (filters.dateFrom) params.set('date_from', filters.dateFrom);
      if (filters.dateTo) params.set('date_to', filters.dateTo);
      const qs = params.toString();
      return api.request<{ sessions: SessionListEntry[] }>(qs ? `/?${qs}` : '/');
    },
    ...QUERY_OPTS,
  });

  const sessions = useMemo(() => {
    const all = listQuery.data?.sessions ?? [];
    if (!filters.search) return all;
    const needle = filters.search.toLowerCase();
    return all.filter((s) => s.conversation_id.toLowerCase().includes(needle));
  }, [listQuery.data, filters.search]);

  const detailQuery = useQuery({
    queryKey: ['sessions-detail', selected],
    queryFn: () => api.request<{ session: SessionDetailView }>(`/${selected}`),
    enabled: Boolean(selected),
    ...QUERY_OPTS,
  });

  const loadError = listQuery.error || detailQuery.error;

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-6 flex items-center gap-2">
        <Activity className="h-6 w-6 text-text-primary" />
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">Sessions</h1>
          <p className="mt-1 text-sm text-text-secondary">
            What actually happened during each AI interaction: request, tool filtering,
            provider/model routing, fallback, tool calls, tokens, latency, and feedback --
            all observed, never simulated.
          </p>
        </div>
      </div>

      {loadError && (
        <Banner>
          <AlertCircle className="mr-2 inline h-4 w-4" />
          {(loadError as Error).message}
        </Banner>
      )}

      <FiltersBar filters={filters} onChange={setFilters} />

      {listQuery.isLoading ? (
        <Spinner className="h-6 w-6" />
      ) : (
        <div className="grid grid-cols-1 gap-6">
          <SessionTable sessions={sessions} selected={selected} onSelect={setSelected} />
          <div className="rounded-xl border border-border-light bg-surface-primary p-5">
            {!selected ? (
              <p className="text-sm text-text-secondary">Select a session to inspect its timeline.</p>
            ) : detailQuery.isLoading ? (
              <Spinner className="h-6 w-6" />
            ) : detailQuery.data ? (
              <SessionDetailPanel view={detailQuery.data.session} />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
