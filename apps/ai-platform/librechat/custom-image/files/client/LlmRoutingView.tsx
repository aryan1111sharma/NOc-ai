/**
 * LLM Routing management page (ADMIN only).
 *
 * Flow: LLM Routing -> list existing agents -> select an agent -> manage that
 * agent's fallback Combo. A Combo target is always an {endpoint, model} pair
 * picked from the read-only Catalog (GET /catalog, itself sourced from
 * LibreChat's own already-registered provider credentials -- librechat.yaml
 * plus anything since added through LibreChat's own Admin Panel, e.g. a new
 * OpenRouter entry) -- this page can never accept, store, or display an API
 * key, and never lets an admin type a provider/base_url/model by hand.
 *
 * Holds no routing logic of its own: every action maps 1:1 onto an endpoint
 * that delegates to llm-router (services/llm-router) or to LibreChat's own
 * Agent CRUD, so the UI can never disagree with the backend about what a
 * Combo is.
 *
 * Styling reuses LibreChat's own tokens (surface-*, text-*, border-*) and the
 * shared @librechat/client primitives, the same as KnowledgeBasesView.tsx
 * beside this file. Server-side authorization is the real boundary; this
 * page being hidden from non-admins is convenience, not security.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, ArrowDown, ArrowUp, Network, Plus, Trash2 } from 'lucide-react';
import { Button, Spinner } from '@librechat/client';
import { request } from 'librechat-data-provider';

type AgentSummary = {
  id: string;
  name: string;
  provider?: string;
  model?: string;
};

type CatalogTarget = {
  endpoint: string;
  model: string;
};

type ComboTarget = {
  endpoint: string;
  model: string;
  enabled: boolean;
};

type Combo = {
  agent_id: string;
  targets: ComboTarget[];
  enabled: boolean;
};

/** Same reasoning as KnowledgeBasesView.tsx's `api` helper: `request` carries the
 *  bearer token requireJwtAuth expects, a bare fetch would not. */
const api = {
  async request<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
    const url = `/api/llm-routing${path}`;
    const method = (init?.method ?? 'GET').toUpperCase();
    try {
      if (method === 'PUT') {
        return (await request.put(url, init?.body ?? {})) as T;
      }
      if (method === 'DELETE') {
        return (await request.delete(url)) as T;
      }
      return (await request.get(url)) as T;
    } catch (e: any) {
      if (e?.response?.status === 404) {
        throw Object.assign(new Error(e?.response?.data?.message ?? 'Not found'), {
          status: 404,
        });
      }
      throw new Error(e?.response?.data?.message ?? e?.message ?? 'LLM routing request failed');
    }
  },
};

const QUERY_OPTS = { retry: 1, refetchOnWindowFocus: false } as const;
const targetKey = (t: { endpoint: string; model: string }) => `${t.endpoint}::${t.model}`;

function Banner({ kind, children }: { kind: 'error' | 'info'; children: React.ReactNode }) {
  const tone =
    kind === 'error'
      ? 'border-red-500/40 bg-red-500/10 text-red-500'
      : 'border-border-medium bg-surface-secondary text-text-secondary';
  return <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${tone}`}>{children}</div>;
}

/* -------------------------------------------------------------------------- */
/* Agent list                                                                  */
/* -------------------------------------------------------------------------- */

function AgentList({
  agents,
  combosByAgent,
  selectedId,
  onSelect,
}: {
  agents: AgentSummary[];
  combosByAgent: Map<string, Combo>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (agents.length === 0) {
    return <p className="text-sm text-text-secondary">No agents exist yet.</p>;
  }
  return (
    <div className="flex flex-col gap-1">
      {agents.map((agent) => {
        const combo = combosByAgent.get(agent.id);
        const active = agent.id === selectedId;
        return (
          <button
            key={agent.id}
            type="button"
            onClick={() => onSelect(agent.id)}
            className={`flex items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
              active
                ? 'border-border-heavy bg-surface-tertiary text-text-primary'
                : 'border-border-light bg-surface-primary text-text-secondary hover:bg-surface-secondary'
            }`}
          >
            <span className="truncate">{agent.name || agent.id}</span>
            {combo && (
              <span
                className={`ml-2 shrink-0 rounded-full px-2 py-0.5 text-xs ${
                  combo.enabled
                    ? 'bg-green-500/15 text-green-600'
                    : 'bg-surface-tertiary text-text-secondary'
                }`}
              >
                {combo.enabled ? `${combo.targets.length} target(s)` : 'disabled'}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Combo editor                                                               */
/* -------------------------------------------------------------------------- */

function ComboEditor({ agent, catalog }: { agent: AgentSummary; catalog: CatalogTarget[] }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [pendingTarget, setPendingTarget] = useState('');

  const comboQuery = useQuery({
    queryKey: ['llm-combo', agent.id],
    queryFn: async () => {
      try {
        return await api.request<{ combo: Combo }>(`/combos/${encodeURIComponent(agent.id)}`);
      } catch (e: any) {
        if (e?.status === 404) {
          return { combo: null as Combo | null };
        }
        throw e;
      }
    },
    ...QUERY_OPTS,
  });

  const combo = comboQuery.data?.combo ?? null;
  const [targets, setTargets] = useState<ComboTarget[] | null>(null);
  const activeTargets = targets ?? combo?.targets ?? [];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['llm-combo', agent.id] });
    queryClient.invalidateQueries({ queryKey: ['llm-combos'] });
    queryClient.invalidateQueries({ queryKey: ['llm-agents'] });
  };

  const save = useMutation({
    mutationFn: (nextTargets: ComboTarget[]) =>
      api.request<{ combo: Combo }>(`/combos/${encodeURIComponent(agent.id)}`, {
        method: 'PUT',
        body: { targets: nextTargets, enabled: combo?.enabled ?? true },
      }),
    onSuccess: () => {
      setTargets(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const setComboEnabled = useMutation({
    mutationFn: (enabled: boolean) =>
      api.request<{ combo: Combo }>(`/combos/${encodeURIComponent(agent.id)}/enabled`, {
        method: 'PUT',
        body: { enabled },
      }),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => api.request(`/combos/${encodeURIComponent(agent.id)}`, { method: 'DELETE' }),
    onSuccess: () => {
      setTargets(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const usedKeys = new Set(activeTargets.map(targetKey));
  const availableCatalog = catalog.filter((t) => !usedKeys.has(targetKey(t)));

  const mutate = (fn: (prev: ComboTarget[]) => ComboTarget[]) => {
    setError(null);
    setTargets(fn(activeTargets));
  };

  const addTarget = () => {
    if (!pendingTarget) return;
    const found = catalog.find((t) => targetKey(t) === pendingTarget);
    if (!found) return;
    mutate((prev) => [...prev, { ...found, enabled: true }]);
    setPendingTarget('');
  };

  const moveTarget = (index: number, delta: number) => {
    mutate((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const toggleTarget = (index: number) => {
    mutate((prev) => prev.map((t, i) => (i === index ? { ...t, enabled: !t.enabled } : t)));
  };

  const removeTarget = (index: number) => {
    mutate((prev) => prev.filter((_, i) => i !== index));
  };

  const dirty = targets !== null;

  if (comboQuery.isLoading) {
    return <Spinner className="h-5 w-5" />;
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">{agent.name || agent.id}</h2>
          <p className="text-xs text-text-secondary">{agent.id}</p>
        </div>
        {combo && (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              disabled={setComboEnabled.isLoading}
              onClick={() => setComboEnabled.mutate(!combo.enabled)}
            >
              {combo.enabled ? 'Disable combo' : 'Enable combo'}
            </Button>
            <Button
              variant="outline"
              disabled={remove.isLoading}
              onClick={() => {
                if (window.confirm(`Delete the fallback combo for "${agent.name}"?`)) {
                  remove.mutate();
                }
              }}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>

      {error && <Banner kind="error">{error}</Banner>}

      {activeTargets.length === 0 ? (
        <p className="mb-3 text-sm text-text-secondary">
          No fallback targets yet. Add one below -- the first target is tried first.
        </p>
      ) : (
        <ol className="mb-3 flex flex-col gap-2">
          {activeTargets.map((t, i) => (
            <li
              key={targetKey(t)}
              className="flex items-center justify-between rounded-lg border border-border-light bg-surface-primary px-3 py-2"
            >
              <div className="flex items-center gap-3">
                <span className="w-5 shrink-0 text-center text-xs text-text-secondary">
                  {i + 1}
                </span>
                <div>
                  <div className="text-sm text-text-primary">{t.endpoint}</div>
                  <div className="text-xs text-text-secondary">{t.model}</div>
                </div>
                {!t.enabled && (
                  <span className="rounded-full bg-surface-tertiary px-2 py-0.5 text-xs text-text-secondary">
                    disabled
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <Button variant="outline" disabled={i === 0} onClick={() => moveTarget(i, -1)}>
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  disabled={i === activeTargets.length - 1}
                  onClick={() => moveTarget(i, 1)}
                >
                  <ArrowDown className="h-4 w-4" />
                </Button>
                <Button variant="outline" onClick={() => toggleTarget(i)}>
                  {t.enabled ? 'Disable' : 'Enable'}
                </Button>
                <Button variant="outline" onClick={() => removeTarget(i)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}

      <div className="mb-4 flex items-center gap-2">
        <select
          value={pendingTarget}
          onChange={(e) => setPendingTarget(e.target.value)}
          className="flex-1 rounded-lg border border-border-medium bg-surface-secondary px-3 py-2 text-sm text-text-primary outline-none focus:border-border-heavy"
        >
          <option value="">
            {availableCatalog.length === 0
              ? 'No more registered credential/model targets'
              : 'Add a registered credential + model...'}
          </option>
          {availableCatalog.map((t) => (
            <option key={targetKey(t)} value={targetKey(t)}>
              {t.endpoint} / {t.model}
            </option>
          ))}
        </select>
        <Button variant="outline" disabled={!pendingTarget} onClick={addTarget}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex justify-end gap-2">
        {dirty && (
          <Button variant="outline" onClick={() => setTargets(null)}>
            Discard changes
          </Button>
        )}
        <Button
          disabled={activeTargets.length === 0 || save.isLoading || !dirty}
          onClick={() => save.mutate(activeTargets)}
        >
          {save.isLoading ? <Spinner className="h-4 w-4" /> : 'Save combo'}
        </Button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function LlmRoutingView() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const agentsQuery = useQuery({
    queryKey: ['llm-agents'],
    queryFn: () => api.request<{ agents: AgentSummary[] }>('/agents'),
    ...QUERY_OPTS,
  });

  const catalogQuery = useQuery({
    queryKey: ['llm-catalog'],
    queryFn: () => api.request<{ targets: CatalogTarget[] }>('/catalog'),
    ...QUERY_OPTS,
  });

  const combosQuery = useQuery({
    queryKey: ['llm-combos'],
    queryFn: () => api.request<{ combos: Combo[] }>('/combos'),
    ...QUERY_OPTS,
  });

  const agents = agentsQuery.data?.agents ?? [];
  const catalog = catalogQuery.data?.targets ?? [];
  const combosByAgent = useMemo(
    () => new Map((combosQuery.data?.combos ?? []).map((c) => [c.agent_id, c])),
    [combosQuery.data],
  );
  const selectedAgent = agents.find((a) => a.id === selectedId) ?? null;

  const loading = agentsQuery.isLoading || catalogQuery.isLoading;
  const loadError = agentsQuery.error || catalogQuery.error || combosQuery.error;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-center gap-2">
        <Network className="h-6 w-6 text-text-primary" />
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">LLM Routing</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Pick an agent, then build its ordered provider fallback combo from credentials and
            models already registered in this deployment. API keys are never shown or entered
            here.
          </p>
        </div>
      </div>

      {loadError && (
        <Banner kind="error">
          <AlertCircle className="mr-2 inline h-4 w-4" />
          {(loadError as Error).message}
        </Banner>
      )}

      {loading ? (
        <Spinner className="h-6 w-6" />
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-[280px_1fr]">
          <AgentList
            agents={agents}
            combosByAgent={combosByAgent}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <div className="rounded-xl border border-border-light bg-surface-primary p-5">
            {selectedAgent ? (
              <ComboEditor agent={selectedAgent} catalog={catalog} />
            ) : (
              <p className="text-sm text-text-secondary">
                Select an agent on the left to manage its fallback combo.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
