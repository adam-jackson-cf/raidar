import { useEffect, useMemo, useRef, useState, type ChangeEvent, type RefObject } from 'react';
import { ChevronDown, ChevronRight, MessageSquarePlus, Search, SlidersHorizontal, X } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { GateChips } from '@/components/GateChips';
import { SearchPanel } from '@/components/SearchPanel';
import { SpanTree } from '@/components/SpanTree';
import { C } from '@/utils/colors';
import { api } from '@/api/client';
import type { Annotation, AnnotationKind, RunRecord, RunDetail as RunDetailData, Span } from '@/utils/types';
import { WireframeAnnotationCards } from './wireframe-components/WireframeAnnotationCards';
import { WireframeRunListItem } from './wireframe-components/WireframeRunListItem';
import { WireframeRunHeader } from './wireframe-components/WireframeRunHeader';
import { WireframeScorecardPanel } from './wireframe-components/WireframeScorecardPanel';
import { WireframeSpanDetail } from './wireframe-components/WireframeSpanDetail';
import { compactSpec } from './wireframe-components/wireframeLabels';

const KIND_ORDER: Record<Annotation['kind'], number> = { issue: 0, note: 1, good: 2 };
type SyntheticFilterValue = 'synthetic' | 'real';

const SYNTHETIC_FILTER_OPTIONS: Array<{ value: SyntheticFilterValue; label: string }> = [
  { value: 'synthetic', label: 'synthetic' },
  { value: 'real', label: 'not synthetic' },
];

function sortedAnnotations(annotations: Annotation[]): Annotation[] {
  return [...annotations].sort(
    (a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.created_at - b.created_at,
  );
}

function providerValue(run: RunRecord) {
  return run.model.includes('/') ? run.model.split('/')[0] : run.harness;
}

function providerLabel(value: string) {
  if (value.toLowerCase() === 'openai') return 'OpenAI';
  if (value.toLowerCase() === 'anthropic') return 'Anthropic';
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function syntheticFilterValue(run: RunRecord): SyntheticFilterValue {
  return run.synthetic ? 'synthetic' : 'real';
}

function runGroupKey(run: RunRecord) {
  return `${run.scenario}::${run.revision}::${run.agent_spec}`;
}

function WireframeRunList({
  runs,
  selectedRunId,
  onSelectRun,
  isLoading,
}: {
  runs: RunRecord[];
  selectedRunId: string | undefined;
  isLoading: boolean;
  onSelectRun: (id: string) => void;
}) {
  const [scenarioSearch, setScenarioSearch] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);
  const [selectedProviders, setSelectedProviders] = useState<string[]>([]);
  const [selectedAgentSpecs, setSelectedAgentSpecs] = useState<string[]>([]);
  const [selectedSynthetic, setSelectedSynthetic] = useState<SyntheticFilterValue[]>(
    SYNTHETIC_FILTER_OPTIONS.map((option) => option.value),
  );
  const [collapsedScenarios, setCollapsedScenarios] = useState<Set<string>>(new Set());
  const [collapsedRevisions, setCollapsedRevisions] = useState<Set<string>>(new Set());
  const [collapsedAgentSpecs, setCollapsedAgentSpecs] = useState<Set<string>>(new Set());
  const q = scenarioSearch.trim().toLowerCase();
  const scenarios = useMemo(() => [...new Set(runs.map((run) => run.scenario))].sort(), [runs]);
  const providerOptions = useMemo(() => [...new Set(runs.map(providerValue))].sort(), [runs]);
  const agentSpecOptions = useMemo(() => [...new Set(runs.map((run) => run.agent_spec))].sort(), [runs]);

  useEffect(() => {
    setSelectedProviders((current) => (current.length === 0 ? providerOptions : current.filter((item) => providerOptions.includes(item))));
  }, [providerOptions]);

  useEffect(() => {
    setSelectedAgentSpecs((current) => (current.length === 0 ? agentSpecOptions : current.filter((item) => agentSpecOptions.includes(item))));
  }, [agentSpecOptions]);

  useEffect(() => {
    if (!filterOpen) return;
    const close = (event: MouseEvent) => {
      if (!(event.target as HTMLElement).closest('[data-wireframe-runs-filter]')) {
        setFilterOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [filterOpen]);

  const scenarioSuggestion = useMemo(() => {
    if (!q) return '';
    const match = scenarios.find((scenario) => scenario.toLowerCase().startsWith(q));
    if (!match || match.toLowerCase() === q) return '';
    return match;
  }, [q, scenarios]);
  const suggestionSuffix = scenarioSuggestion ? scenarioSuggestion.slice(scenarioSearch.length) : '';

  const filtered = useMemo(() => {
    return runs.filter((run) => {
      const providerSelected = selectedProviders.length === 0 || selectedProviders.includes(providerValue(run));
      const agentSpecSelected = selectedAgentSpecs.length === 0 || selectedAgentSpecs.includes(run.agent_spec);
      const syntheticSelected = selectedSynthetic.includes(syntheticFilterValue(run));
      if (!providerSelected || !agentSpecSelected || !syntheticSelected) return false;
      return !q || run.scenario.toLowerCase().includes(q);
    });
  }, [runs, q, selectedAgentSpecs, selectedProviders, selectedSynthetic]);

  const grouped = useMemo(() => {
    const byExperiment = new Map<
      string,
      { scenario: string; revision: string; experimentId: string; agentSpec: string; runs: RunRecord[] }
    >();
    for (const run of filtered) {
      const key = runGroupKey(run);
      const entry =
        byExperiment.get(key) ?? {
          scenario: run.scenario,
          revision: run.revision,
          experimentId: key,
          agentSpec: run.agent_spec,
          runs: [],
        };
      entry.runs.push(run);
      byExperiment.set(key, entry);
    }

    const byScenario = new Map<
      string,
      {
        scenario: string;
        synthetic: boolean;
        revisions: Array<{
          revision: string;
          blocks: Array<{ revision: string; experimentId: string; agentSpec: string; runs: RunRecord[] }>;
        }>;
      }
    >();
    for (const block of byExperiment.values()) {
      const scenario = byScenario.get(block.scenario) ?? {
        scenario: block.scenario,
        synthetic: false,
        revisions: [],
      };
      scenario.synthetic = scenario.synthetic || block.runs.some((run) => run.synthetic);
      const revision = scenario.revisions.find((item) => item.revision === block.revision) ?? {
        revision: block.revision,
        blocks: [],
      };
      revision.blocks.push({
        revision: block.revision,
        experimentId: block.experimentId,
        agentSpec: block.agentSpec,
        runs: block.runs,
      });
      if (!scenario.revisions.includes(revision)) scenario.revisions.push(revision);
      byScenario.set(block.scenario, scenario);
    }
    return [...byScenario.values()];
  }, [filtered]);

  const toggleCollapsed = (setter: (value: Set<string>) => void, current: Set<string>, key: string) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  };

  return (
    <aside
      className="flex h-full min-h-0 w-80 shrink-0 flex-col gap-2 border-r p-2"
      style={{ borderColor: C.border, background: C.surface }}
    >
      <div className="flex items-center gap-2 rounded border px-2 py-1" style={{ borderColor: C.border, background: 'rgba(0,0,0,0.35)' }}>
          <Search className="size-3.5 shrink-0" style={{ color: C.fg0 }} />
          <div className="relative min-w-0 flex-1">
            {scenarioSearch && suggestionSuffix ? (
              <div className="pointer-events-none absolute inset-0 flex items-center text-xs">
                <span className="invisible whitespace-pre">{scenarioSearch}</span>
                <span style={{ color: C.fg0 }}>{suggestionSuffix}</span>
              </div>
            ) : null}
            <input
              value={scenarioSearch}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setScenarioSearch(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === 'Tab' && scenarioSuggestion) {
                  event.preventDefault();
                  setScenarioSearch(scenarioSuggestion);
                }
              }}
              placeholder="Search scenarios"
              className="relative w-full bg-transparent text-xs outline-none"
              style={{ color: C.fg4 }}
            />
          </div>
          <div className="relative shrink-0" data-wireframe-runs-filter>
            <button
              type="button"
              className="inline-flex size-6 items-center justify-center rounded-md border transition hover:bg-white/5"
              style={{ borderColor: filterOpen ? C.selectedBorder : C.border, color: filterOpen ? C.fg4 : C.fg2, background: filterOpen ? 'rgba(255,255,255,0.05)' : 'transparent' }}
              aria-label="Filter runs"
              title="Filter runs"
              onClick={() => setFilterOpen((open) => !open)}
            >
              <SlidersHorizontal size={13} />
            </button>
            {filterOpen ? (
              <div
                className="absolute left-auto right-0 top-full z-30 mt-1 w-64 rounded-md border p-2 text-[10px] shadow-xl"
                style={{ borderColor: C.selectedBorder, background: 'rgba(0,0,0,0.94)' }}
              >
                <div className="grid gap-2">
                  <div>
                    <div className="mb-1 text-[9px] font-semibold uppercase tracking-wide" style={{ color: C.fg0 }}>
                      Provider
                    </div>
                    <div className="space-y-1">
                      {providerOptions.map((provider) => (
                        <label key={provider} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-white/5" style={{ color: C.fg2 }}>
                          <input
                            type="checkbox"
                            checked={selectedProviders.includes(provider)}
                            onChange={() => {
                              setSelectedProviders((current) => {
                                if (!current.includes(provider)) return [...current, provider];
                                return current.length === 1 ? current : current.filter((item) => item !== provider);
                              });
                            }}
                          />
                          {providerLabel(provider)}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1 text-[9px] font-semibold uppercase tracking-wide" style={{ color: C.fg0 }}>
                      Agent spec
                    </div>
                    <div className="max-h-32 space-y-1 overflow-auto">
                      {agentSpecOptions.map((agentSpec) => (
                        <label key={agentSpec} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-white/5" style={{ color: C.fg2 }}>
                          <input
                            type="checkbox"
                            checked={selectedAgentSpecs.includes(agentSpec)}
                            onChange={() => {
                              setSelectedAgentSpecs((current) => {
                                if (!current.includes(agentSpec)) return [...current, agentSpec];
                                return current.length === 1 ? current : current.filter((item) => item !== agentSpec);
                              });
                            }}
                          />
                          <span className="truncate" title={agentSpec}>{compactSpec(agentSpec)}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1 text-[9px] font-semibold uppercase tracking-wide" style={{ color: C.fg0 }}>
                      Synthetic
                    </div>
                    <div className="space-y-1">
                      {SYNTHETIC_FILTER_OPTIONS.map(({ value, label }) => (
                        <label key={value} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-white/5" style={{ color: C.fg2 }}>
                          <input
                            type="checkbox"
                            checked={selectedSynthetic.includes(value)}
                            onChange={() => {
                              setSelectedSynthetic((current) => {
                                if (!current.includes(value)) return [...current, value];
                                return current.length === 1 ? current : current.filter((item) => item !== value);
                              });
                            }}
                          />
                          {label}
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
      </div>
      <div className="sb flex min-h-0 flex-1 flex-col gap-1 overflow-auto rounded border" style={{ borderColor: C.border }}>
        {isLoading && (
          <div className="p-2 text-xs" style={{ color: C.fg1 }}>
            Loading runs…
          </div>
        )}
        {!isLoading && grouped.length === 0 && (
          <div className="p-2 text-xs" style={{ color: C.fg0 }}>
            No runs match this filter.
          </div>
        )}
        {grouped.map((scenario) => (
          <div key={scenario.scenario} className="flex flex-col gap-2 border-b pb-2 last:border-b-0" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
            <div className="flex items-center gap-1.5 px-2 pt-1 text-xs font-semibold" style={{ color: C.fg2 }}>
              <button
                type="button"
                className="inline-flex size-4 shrink-0 items-center justify-center rounded hover:bg-white/5"
                aria-label={collapsedScenarios.has(scenario.scenario) ? 'Expand scenario' : 'Collapse scenario'}
                onClick={() => toggleCollapsed(setCollapsedScenarios, collapsedScenarios, scenario.scenario)}
              >
                {collapsedScenarios.has(scenario.scenario) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
              </button>
              <span className="min-w-0 truncate">{scenario.scenario}</span>
              {scenario.synthetic ? (
                <span
                  className="rounded border px-1 py-0.5 text-[8px] font-semibold uppercase tracking-wide"
                  style={{
                    color: '#f59e0b',
                    borderColor: '#f59e0b66',
                    background: 'rgba(245, 158, 11, 0.16)',
                  }}
                >
                  synth
                </span>
              ) : null}
            </div>
            {!collapsedScenarios.has(scenario.scenario) && scenario.revisions.map((revision) => {
              const revisionKey = `${scenario.scenario}:${revision.revision}`;
              const revisionRunCount = revision.blocks.reduce((total, block) => total + block.runs.length, 0);
              return (
                <div key={revisionKey} className="flex flex-col gap-1">
                  <div className="flex items-center gap-1.5 px-2 text-[10px]" style={{ color: C.fg1 }}>
                    <button
                      type="button"
                      className="inline-flex size-4 shrink-0 items-center justify-center rounded hover:bg-white/5"
                      aria-label={collapsedRevisions.has(revisionKey) ? 'Expand revision' : 'Collapse revision'}
                      onClick={() => toggleCollapsed(setCollapsedRevisions, collapsedRevisions, revisionKey)}
                    >
                      {collapsedRevisions.has(revisionKey) ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                    </button>
                    <span>{revision.revision}</span>
                    <span className="ml-auto">{revisionRunCount} runs</span>
                  </div>
                  {!collapsedRevisions.has(revisionKey) && revision.blocks.map((block) => {
                    const blockKey = `${revisionKey}:${block.experimentId}`;
                    return (
                      <div key={block.experimentId} className="flex flex-col gap-1">
                        <div className="flex items-center gap-1.5 px-2 text-[9px]" style={{ color: C.fg1 }}>
                          <button
                            type="button"
                            className="inline-flex size-4 shrink-0 items-center justify-center rounded hover:bg-white/5"
                            aria-label={collapsedAgentSpecs.has(blockKey) ? 'Expand agent spec' : 'Collapse agent spec'}
                            onClick={() => toggleCollapsed(setCollapsedAgentSpecs, collapsedAgentSpecs, blockKey)}
                          >
                            {collapsedAgentSpecs.has(blockKey) ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                          </button>
                          <span className="truncate text-[10px]" style={{ color: C.cyan }} title={block.agentSpec}>
                            {compactSpec(block.agentSpec)}
                          </span>
                          <span className="ml-auto">{block.runs.length} runs</span>
                        </div>
                        {!collapsedAgentSpecs.has(blockKey) ? (
                          <div className="flex flex-col gap-1 px-1">
                            {block.runs.map((run, index) => (
                              <WireframeRunListItem
                                key={run.id}
                                run={run}
                                previousRun={block.runs[index - 1]}
                                selected={selectedRunId === run.id}
                                onClick={() => onSelectRun(run.id)}
                              />
                            ))}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </aside>
  );
}

function WireframeAnnotationModal({
  targetSpan,
  pending,
  textareaRef,
  onClose,
  onSubmit,
}: {
  targetSpan: Span | null;
  pending: boolean;
  textareaRef: RefObject<HTMLTextAreaElement>;
  onClose: () => void;
  onSubmit: (note: string) => void;
}) {
  const [note, setNote] = useState('');

  const save = () => {
    const trimmed = note.trim();
    if (!trimmed || pending) return;
    onSubmit(trimmed);
  };

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-end bg-black/35 p-4">
      <div
        className="w-full max-w-md rounded-lg border p-3 shadow-2xl"
        style={{ background: 'rgba(12,12,13,0.98)', borderColor: C.border }}
      >
        <div className="mb-2 flex items-center gap-2">
          <MessageSquarePlus className="size-3.5" style={{ color: C.accent }} />
          <span className="text-xs font-medium" style={{ color: C.fg4 }}>
            {targetSpan ? 'Annotate this span' : 'Annotate this run'}
          </span>
          <span className="min-w-0 flex-1 truncate text-[10px]" style={{ color: C.fg0 }}>
            {targetSpan ? targetSpan.name : 'run note'}
          </span>
          <button
            type="button"
            className="inline-flex size-6 items-center justify-center rounded border hover:bg-white/5"
            style={{ borderColor: C.border, color: C.fg2 }}
            onClick={onClose}
            aria-label="Close annotation form"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <textarea
          ref={textareaRef}
          value={note}
          onChange={(event) => setNote(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              save();
            }
          }}
          placeholder="What did you notice?"
          className="sb min-h-[88px] w-full resize-y rounded-md px-2 py-1.5 text-xs outline-none"
          style={{ border: `1px solid ${C.border}`, background: 'rgba(0,0,0,0.45)', color: C.fg4 }}
        />
        <div className="mt-2 flex items-center justify-end gap-2">
          <button
            type="button"
            className="rounded-md border px-2.5 py-1 text-[11px]"
            style={{ borderColor: C.border, color: C.fg2 }}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={save}
            disabled={pending || note.trim().length === 0}
            className="rounded-md px-2.5 py-1 text-[11px] font-medium disabled:opacity-40"
            style={{ background: C.fg5, color: '#000', border: `1px solid ${C.fg5}` }}
          >
            {pending ? 'Saving...' : 'Save note'}
          </button>
        </div>
      </div>
    </div>
  );
}

function WireframeRunDetailPanel({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedSpanId = searchParams.get('span');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [findingsOpen, setFindingsOpen] = useState(false);
  const [annotationTarget, setAnnotationTarget] = useState<Span | null | undefined>(undefined);

  const detail = useQuery({
    queryKey: ['run-detail', runId],
    queryFn: () => api.runDetail(runId),
  });

  const invalidateDetail = () => {
    void queryClient.invalidateQueries({ queryKey: ['run-detail', runId] });
    void queryClient.invalidateQueries({ queryKey: ['annotations', runId] });
  };

  const createMutation = useMutation({
    mutationFn: (input: { kind: AnnotationKind; note: string; span_id: string | null }) =>
      api.createAnnotation({ run_id: runId, span_id: input.span_id, kind: input.kind, note: input.note }),
    onSuccess: invalidateDetail,
  });

  const selectSpan = (spanId: string | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (spanId) next.set('span', spanId);
        else next.delete('span');
        return next;
      },
      { replace: true },
    );
  };

  if (detail.isLoading) {
    return <div className="flex h-full items-center justify-center text-xs" style={{ color: C.fg1 }}>Loading run…</div>;
  }

  if (detail.isError || !detail.data) {
    const message = (detail.error as Error | null)?.message ?? 'unknown error';
    return (
      <div className="flex h-full items-center justify-center text-xs" style={{ color: C.red }}>
        {`Failed to load run: ${message}`}
      </div>
    );
  }

  const data: RunDetailData = detail.data;
  const selectedSpan = selectedSpanId
    ? (data.spans.find((s) => s.id === selectedSpanId) ?? null)
    : null;
  const annotations = sortedAnnotations(data.annotations);
  const spanNameById = new Map(data.spans.map((s) => [s.id, s.name]));

  return (
    <main className="min-w-0 flex-1 overflow-hidden">
      <div className="sb flex h-full flex-col gap-3 overflow-auto p-3">
        <WireframeRunHeader
          run={data.run}
          onAnnotateRun={() => setAnnotationTarget(null)}
        >
          <GateChips spans={data.spans} onSelect={(spanId) => selectSpan(spanId)} />
        </WireframeRunHeader>

          <WireframeScorecardPanel
            spans={data.spans}
            onSelect={(spanId) => selectSpan(spanId)}
          />

        {annotations.length > 0 && (
          <section
            className="rounded-lg p-2.5"
            style={{ background: C.surface, border: `1px solid ${C.border}` }}
          >
            <button
              className="mb-2 flex items-center gap-2 text-left"
              onClick={() => setFindingsOpen((open) => !open)}
            >
              {findingsOpen ? (
                <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
              ) : (
                <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
              )}
              <span className="text-xs font-medium" style={{ color: C.fg3 }}>
                Findings
              </span>
            </button>

            {findingsOpen && (
              <div className="sb grid max-h-72 gap-1.5 overflow-auto xl:grid-cols-2">
                <WireframeAnnotationCards
                  annotations={annotations}
                  spanNameById={spanNameById}
                  onJumpToSpan={(spanId) => selectSpan(spanId)}
                />
              </div>
            )}
          </section>
        )}

        <section
          className="flex min-h-[420px] flex-col overflow-hidden rounded-lg"
          style={{ border: `1px solid ${C.border}` }}
        >
          <SearchPanel runId={runId} onSelect={(spanId) => selectSpan(spanId)} frameless />
          <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
            <div
              className="min-h-0 flex-1 lg:basis-[60%]"
              style={{ borderRight: selectedSpan ? `1px solid ${C.border}` : 'none' }}
            >
              <SpanTree
                spans={data.spans}
                annotations={data.annotations}
                selectedSpanId={selectedSpanId}
                onSelect={(spanId) => selectSpan(spanId)}
              />
            </div>
            {selectedSpan && (
              <div className="min-h-0 lg:basis-[40%]" style={{ background: C.surface }}>
                <WireframeSpanDetail
                  span={selectedSpan}
                  annotations={data.annotations}
                  onAnnotate={() => setAnnotationTarget(selectedSpan)}
                />
              </div>
            )}
          </div>
        </section>
        {annotationTarget !== undefined ? (
          <WireframeAnnotationModal
            targetSpan={annotationTarget}
            pending={createMutation.isPending}
            textareaRef={textareaRef}
            onClose={() => setAnnotationTarget(undefined)}
            onSubmit={(note) => {
              createMutation.mutate(
                { kind: 'note', note, span_id: annotationTarget?.id ?? null },
                { onSuccess: () => setAnnotationTarget(undefined) },
              );
            }}
          />
        ) : null}
      </div>
    </main>
  );
}

export function WireframeRunsPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const runs = useQuery({ queryKey: ['runs', 'wireframe'], queryFn: api.runs });

  const onSelectRun = (id: string) => {
    navigate(`/wireframe/runs/${encodeURIComponent(id)}`);
  };

  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden" style={{ borderTop: `1px solid ${C.border}` }}>
      <WireframeRunList
        runs={runs.data ?? []}
        isLoading={runs.isLoading}
        selectedRunId={runId}
        onSelectRun={onSelectRun}
      />
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
        {runId ? (
          <WireframeRunDetailPanel runId={runId} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
            <span className="text-sm" style={{ color: C.fg3 }}>
              Pick a run to inspect score, findings, and execution traces.
            </span>
            <span className="max-w-sm text-xs leading-relaxed" style={{ color: C.fg0 }}>
              Use the scenario selector to jump between runs and compare outcomes quickly.
            </span>
            <Link
              to="/wireframe"
              className="text-xs underline decoration-dotted underline-offset-2"
              style={{ color: C.accent }}
            >
              Open wireframe experiments
            </Link>
          </div>
        )}
      </main>
    </div>
  );
}
