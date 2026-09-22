/**
 * LLM Usage / Status view (ADMIN only).
 *
 * A read-only analytical page reachable from the LLM Routing page's "Usage /
 * Status" button. Shows every provider/model already registered in the
 * Catalog (GET /catalog, same source LlmRoutingView.tsx uses) alongside
 * request/token counters this router has itself observed on the request
 * path (GET /usage, services/llm-router's Registry.list_usage).
 *
 * Everything under "Local / observed usage" is traffic THIS router saw,
 * never a provider-reported figure -- GLM's z.ai balance and Kimi's
 * subscription quota are not exposed by either provider's API today (see
 * services/llm-router/README.md and docs/superpowers/specs/
 * 2026-08-06-kimi-endpoint-design.md), so remaining-quota/reset-time are
 * always rendered as "Quota information unavailable", never estimated.
 * `quota` is carried as an explicit null-shaped field so a real per-provider
 * quota resolver can be plugged in later without a UI rewrite.
 *
 * Holds no logic of its own beyond grouping/formatting what the two existing
 * read-only endpoints return -- same "thin view over existing data" shape as
 * LlmRoutingView.tsx beside this file.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, ArrowLeft, Gauge } from 'lucide-react';
import { Button, Spinner } from '@librechat/client';
import { request } from 'librechat-data-provider';

type CostType = 'FREE' | 'PAID' | 'UNKNOWN';

type CatalogTarget = {
  endpoint: string;
  model: string;
  cost_type: CostType;
};

type UsageStats = {
  endpoint: string;
  model: string;
  request_count: number;
  success_count: number;
  failure_count: number;
  last_failure_class: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_used_at: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  // Never populated today -- see the file header. Kept explicit so a real
  // provider quota resolver has a field to fill in without a UI change.
  quota?: { remaining: number; limit: number; resets_at: string } | null;
};

const api = {
  async request<T>(path: string): Promise<T> {
    try {
      return (await request.get(`/api/llm-routing${path}`)) as T;
    } catch (e: any) {
      throw new Error(e?.response?.data?.message ?? e?.message ?? 'LLM usage request failed');
    }
  },
};

const QUERY_OPTS = { retry: 1, refetchOnWindowFocus: false } as const;

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500">
      {children}
    </div>
  );
}

function CostBadge({ costType }: { costType: CostType }) {
  const tone =
    costType === 'FREE'
      ? 'bg-green-500/15 text-green-600'
      : costType === 'PAID'
        ? 'bg-blue-500/15 text-blue-600'
        : 'bg-surface-tertiary text-text-secondary';
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${tone}`}>{costType}</span>;
}

/* -------------------------------------------------------------------------- */
/* Provider list                                                              */
/* -------------------------------------------------------------------------- */

type ProviderGroup = {
  endpoint: string;
  costType: CostType;
  models: CatalogTarget[];
};

/** Derived only from observed request/failure timestamps -- never a
 *  provider health check this router doesn't have. */
function providerStatus(models: CatalogTarget[], usageByTarget: Map<string, UsageStats>) {
  const stats = models
    .map((m) => usageByTarget.get(`${m.endpoint}::${m.model}`))
    .filter((u): u is UsageStats => Boolean(u));
  if (stats.length === 0) {
    return { label: 'No data observed', tone: 'bg-surface-tertiary text-text-secondary' };
  }
  let mostRecent: { at: string; kind: 'success' | 'failure'; failureClass: string | null } | null =
    null;
  for (const s of stats) {
    for (const [at, kind] of [
      [s.last_success_at, 'success'] as const,
      [s.last_failure_at, 'failure'] as const,
    ]) {
      if (at && (!mostRecent || at > mostRecent.at)) {
        mostRecent = { at, kind, failureClass: s.last_failure_class };
      }
    }
  }
  if (!mostRecent) {
    return { label: 'No data observed', tone: 'bg-surface-tertiary text-text-secondary' };
  }
  if (mostRecent.kind === 'failure') {
    return {
      label: `Recently failed (${mostRecent.failureClass ?? 'unknown'})`,
      tone: 'bg-red-500/15 text-red-600',
    };
  }
  return { label: 'Healthy', tone: 'bg-green-500/15 text-green-600' };
}

function ProviderList({
  providers,
  usageByTarget,
  selected,
  onSelect,
}: {
  providers: ProviderGroup[];
  usageByTarget: Map<string, UsageStats>;
  selected: string | null;
  onSelect: (endpoint: string) => void;
}) {
  if (providers.length === 0) {
    return <p className="text-sm text-text-secondary">No providers registered yet.</p>;
  }
  return (
    <div className="flex flex-col gap-1">
      {providers.map((p) => {
        const status = providerStatus(p.models, usageByTarget);
        const active = p.endpoint === selected;
        return (
          <button
            key={p.endpoint}
            type="button"
            onClick={() => onSelect(p.endpoint)}
            className={`flex flex-col gap-1 rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
              active
                ? 'border-border-heavy bg-surface-tertiary text-text-primary'
                : 'border-border-light bg-surface-primary text-text-secondary hover:bg-surface-secondary'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium text-text-primary">{p.endpoint}</span>
              <CostBadge costType={p.costType} />
            </div>
            <div className="flex items-center justify-between gap-2 text-xs">
              <span>{p.models.length} model(s)</span>
              <span className={`rounded-full px-2 py-0.5 ${status.tone}`}>{status.label}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Model usage detail                                                        */
/* -------------------------------------------------------------------------- */

function ModelUsageRow({ target, usage }: { target: CatalogTarget; usage: UsageStats | undefined }) {
  const hasData = Boolean(usage && usage.request_count > 0);
  return (
    <div className="rounded-lg border border-border-light bg-surface-primary p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-text-primary">{target.model}</span>
        <span className="rounded-full bg-surface-tertiary px-2 py-0.5 text-xs text-text-secondary">
          LOCAL / OBSERVED USAGE
        </span>
      </div>

      {!hasData ? (
        <p className="text-xs text-text-secondary">No requests observed yet.</p>
      ) : (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-text-secondary sm:grid-cols-3">
          <div>Requests: <span className="text-text-primary">{usage!.request_count}</span></div>
          <div>Success: <span className="text-text-primary">{usage!.success_count}</span></div>
          <div>Failure: <span className="text-text-primary">{usage!.failure_count}</span></div>
          <div>Last success: <span className="text-text-primary">{formatTimestamp(usage!.last_success_at)}</span></div>
          <div>Last failure: <span className="text-text-primary">{formatTimestamp(usage!.last_failure_at)}</span></div>
          <div>
            Last failure class:{' '}
            <span className="text-text-primary">{usage!.last_failure_class ?? 'none'}</span>
          </div>
          <div>Last used: <span className="text-text-primary">{formatTimestamp(usage!.last_used_at)}</span></div>
          {usage!.total_tokens > 0 ? (
            <>
              <div>Prompt tokens: <span className="text-text-primary">{usage!.prompt_tokens}</span></div>
              <div>Completion tokens: <span className="text-text-primary">{usage!.completion_tokens}</span></div>
              <div>Total tokens: <span className="text-text-primary">{usage!.total_tokens}</span></div>
            </>
          ) : (
            <div className="col-span-2 sm:col-span-3">
              Tokens: <span className="text-text-primary">not reported by provider</span>
            </div>
          )}
        </div>
      )}

      <p className="mt-2 text-xs italic text-text-secondary">
        Quota information unavailable{usage?.quota ? '' : ' -- not exposed by this provider'}.
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function LlmUsageView() {
  const navigate = useNavigate();
  const [selectedEndpoint, setSelectedEndpoint] = useState<string | null>(null);

  const catalogQuery = useQuery({
    queryKey: ['llm-catalog'],
    queryFn: () => api.request<{ targets: CatalogTarget[] }>('/catalog'),
    ...QUERY_OPTS,
  });

  const usageQuery = useQuery({
    queryKey: ['llm-usage'],
    queryFn: () => api.request<{ usage: UsageStats[] }>('/usage'),
    ...QUERY_OPTS,
  });

  const targets = catalogQuery.data?.targets ?? [];
  const usageByTarget = useMemo(
    () => new Map((usageQuery.data?.usage ?? []).map((u) => [`${u.endpoint}::${u.model}`, u])),
    [usageQuery.data],
  );

  const providers = useMemo<ProviderGroup[]>(() => {
    const byEndpoint = new Map<string, ProviderGroup>();
    for (const t of targets) {
      const existing = byEndpoint.get(t.endpoint);
      if (existing) {
        existing.models.push(t);
      } else {
        byEndpoint.set(t.endpoint, { endpoint: t.endpoint, costType: t.cost_type, models: [t] });
      }
    }
    return Array.from(byEndpoint.values()).sort((a, b) => a.endpoint.localeCompare(b.endpoint));
  }, [targets]);

  const selectedProvider = providers.find((p) => p.endpoint === selectedEndpoint) ?? null;

  const loading = catalogQuery.isLoading || usageQuery.isLoading;
  const loadError = catalogQuery.error || usageQuery.error;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-center gap-2">
        <Button variant="outline" onClick={() => navigate('/llm-routing')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <Gauge className="h-6 w-6 text-text-primary" />
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">LLM Usage / Status</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Registered providers and models, with request/token counters this router has itself
            observed. Provider-reported quota is not available for the currently registered
            providers -- see each model for details.
          </p>
        </div>
      </div>

      {loadError && (
        <Banner>
          <AlertCircle className="mr-2 inline h-4 w-4" />
          {(loadError as Error).message}
        </Banner>
      )}

      {loading ? (
        <Spinner className="h-6 w-6" />
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-[280px_1fr]">
          <ProviderList
            providers={providers}
            usageByTarget={usageByTarget}
            selected={selectedEndpoint}
            onSelect={setSelectedEndpoint}
          />
          <div className="rounded-xl border border-border-light bg-surface-primary p-5">
            {selectedProvider ? (
              <div>
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="text-lg font-semibold text-text-primary">
                    {selectedProvider.endpoint}
                  </h2>
                  <CostBadge costType={selectedProvider.costType} />
                </div>
                <div className="flex flex-col gap-2">
                  {selectedProvider.models.map((t) => (
                    <ModelUsageRow
                      key={`${t.endpoint}::${t.model}`}
                      target={t}
                      usage={usageByTarget.get(`${t.endpoint}::${t.model}`)}
                    />
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-text-secondary">
                Select a provider on the left to inspect its registered models.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
