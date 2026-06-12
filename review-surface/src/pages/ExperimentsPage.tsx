import { Fragment, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronRight, Trophy } from 'lucide-react';
import { api } from '@/api/client';
import { KIND_STYLES } from '@/components/AnnotationChip';
import { EvidenceRefList } from '@/components/AnnotationCards';
import { Badge } from '@/components/Badge';
import { FindingChips } from '@/components/FindingChips';
import { C } from '@/utils/colors';
import { fmtPercent, fmtScore, fmtTokens } from '@/utils/helpers';
import type { AnnotationKind, ExperimentRecord, StatBlock } from '@/utils/types';

function meanStd(stat: StatBlock | undefined): string {
  if (stat?.mean == null) return '—';
  const mean = stat.mean.toFixed(3);
  return stat.stddev != null ? `${mean} ± ${stat.stddev.toFixed(3)}` : mean;
}

function findingCounts(exp: ExperimentRecord): Partial<Record<AnnotationKind, number>> {
  const counts: Partial<Record<AnnotationKind, number>> = {};
  for (const f of exp.findings) counts[f.kind] = (counts[f.kind] ?? 0) + 1;
  return counts;
}

const TH = 'px-2.5 py-1.5 text-left text-[9px] font-medium uppercase tracking-wider';
const TD = 'num px-2.5 py-1.5 text-[11px]';

function ExperimentExpansion({ exp }: { exp: ExperimentRecord }) {
  const metricOutcomes = Object.entries(exp.aggregate.metric_outcomes ?? {});
  const scorerOutcomes = Object.entries(exp.aggregate.scorer_outcomes ?? {});
  return (
    <div className="flex flex-col gap-3 px-3 py-2.5" style={{ background: 'rgba(255,255,255,0.015)' }}>
      {metricOutcomes.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Metric outcomes
          </div>
          <table className="w-full max-w-xl border-collapse">
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                <th className={TH} style={{ color: C.fg0 }}>Metric</th>
                <th className={TH} style={{ color: C.fg0 }}>Pass rate</th>
                <th className={TH} style={{ color: C.fg0 }}>Mean score</th>
                <th className={TH} style={{ color: C.fg0 }}>Samples</th>
              </tr>
            </thead>
            <tbody>
              {metricOutcomes.map(([metric, o]) => (
                <tr key={metric} style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                  <td className={TD} style={{ color: C.fg3 }}>{metric}</td>
                  <td className={TD} style={{ color: o.pass_rate >= 1 ? C.green : o.pass_rate <= 0 ? C.red : C.fg2 }}>
                    {fmtPercent(o.pass_rate)}
                  </td>
                  <td className={TD} style={{ color: C.fg2 }}>{fmtScore(o.mean_score)}</td>
                  <td className={TD} style={{ color: C.fg1 }}>{o.sample_size}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {scorerOutcomes.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Scorer outcomes
          </div>
          <div className="flex flex-wrap gap-1.5">
            {scorerOutcomes.map(([scorer, o]) => (
              <span
                key={scorer}
                className="num inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px]"
                style={{ color: C.fg2, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
              >
                {scorer}
                <span style={{ color: C.accent }}>{fmtScore(o.mean_score)}</span>
                <span style={{ color: C.fg0 }}>n={o.sample_size}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {exp.findings.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Findings
          </div>
          <div className="flex flex-col gap-1.5">
            {exp.findings.map((f) => {
              const style = KIND_STYLES[f.kind];
              return (
                <div
                  key={f.id}
                  className="rounded-lg px-2.5 py-2"
                  style={{
                    border: `1px solid ${style.border}`,
                    background: `linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015)), ${style.bg}`,
                  }}
                >
                  <div className="mb-0.5 flex flex-wrap items-center gap-2">
                    <span
                      className="inline-flex h-[18px] items-center gap-1 rounded-full px-1.5 text-[10px] font-medium leading-[18px]"
                      style={{ color: style.fg, background: style.bg, border: `1px solid ${style.border}` }}
                    >
                      <span className="font-bold">{style.icon}</span>
                      {style.label}
                    </span>
                    {f.category && (
                      <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
                        {f.category}
                      </span>
                    )}
                    <span className="text-xs font-medium" style={{ color: C.fg4 }}>
                      {f.title}
                    </span>
                  </div>
                  {f.detail && (
                    <div className="text-xs leading-relaxed" style={{ color: C.fg3 }}>
                      {f.detail}
                    </div>
                  )}
                  <EvidenceRefList evidence={f.evidence} />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {exp.run_ids.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Runs
          </div>
          <div className="flex flex-wrap gap-1.5">
            {exp.run_ids.map((id) => (
              <Link
                key={id}
                to={`/runs/${encodeURIComponent(id)}`}
                className="num rounded px-1.5 py-0.5 text-[10px] transition hover:bg-white/10"
                style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
              >
                {id}
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ExperimentGroup({ groupKey, experiments }: { groupKey: string; experiments: ExperimentRecord[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const bestComposite = useMemo(() => {
    const means = experiments
      .map((e) => e.aggregate.composite_score?.mean)
      .filter((m): m is number => m != null);
    return means.length > 0 ? Math.max(...means) : null;
  }, [experiments]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <section
      className="overflow-hidden rounded-lg"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: `1px solid ${C.border}` }}>
        <span className="num text-sm font-medium" style={{ color: C.fg5 }}>
          {groupKey}
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          {experiments.length} agent spec{experiments.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="sb overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th className={TH} style={{ color: C.fg0 }}>Agent spec</th>
              <th className={TH} style={{ color: C.fg0 }}>Composite</th>
              <th className={TH} style={{ color: C.fg0 }}>Quality</th>
              <th className={TH} style={{ color: C.fg0 }}>Validity</th>
              <th className={TH} style={{ color: C.fg0 }}>Scored</th>
              <th className={TH} style={{ color: C.fg0 }}>Duration (s)</th>
              <th className={TH} style={{ color: C.fg0 }}>Tokens</th>
              <th className={TH} style={{ color: C.fg0 }}>Adequacy</th>
              <th className={TH} style={{ color: C.fg0 }}>Findings</th>
            </tr>
          </thead>
          <tbody>
            {experiments.map((exp) => {
              const isOpen = expanded.has(exp.experiment_id);
              const compositeMean = exp.aggregate.composite_score?.mean ?? null;
              const isBest =
                bestComposite != null && compositeMean != null && compositeMean === bestComposite;
              const scored = exp.aggregate.run_count_scored ?? 0;
              const total = exp.aggregate.run_count_total ?? 0;
              return (
                <Fragment key={exp.experiment_id}>
                  <tr
                    className="cursor-pointer transition hover:bg-white/[0.03]"
                    style={{
                      borderBottom: '1px solid rgba(255,255,255,0.03)',
                      borderLeft: isBest ? `2px solid ${C.accent}` : '2px solid transparent',
                      background: isBest ? 'rgba(91,141,239,0.04)' : 'transparent',
                    }}
                    onClick={() => toggle(exp.experiment_id)}
                  >
                    <td className={TD}>
                      <span className="flex items-center gap-1.5">
                        <ChevronRight
                          className="size-3 shrink-0 transition-transform"
                          style={{ color: C.fg1, transform: isOpen ? 'rotate(90deg)' : '' }}
                        />
                        <span style={{ color: C.cyan }}>{exp.agent_spec}</span>
                        {isBest && (
                          <Trophy className="size-3" style={{ color: C.accent }} aria-label="Best composite mean" />
                        )}
                        {exp.synthetic && <Badge label="synthetic" />}
                      </span>
                    </td>
                    <td className={TD} style={{ color: isBest ? C.accent : C.fg3, fontWeight: isBest ? 600 : 400 }}>
                      {meanStd(exp.aggregate.composite_score)}
                    </td>
                    <td className={TD} style={{ color: C.fg2 }}>
                      {fmtScore(exp.aggregate.quality_score?.mean)}
                    </td>
                    <td className={TD} style={{ color: C.fg2 }}>
                      {fmtPercent(exp.aggregate.validity_rate)}
                    </td>
                    <td className={TD} style={{ color: C.fg2 }}>
                      {scored}/{total}
                    </td>
                    <td className={TD} style={{ color: C.fg2 }}>
                      {exp.aggregate.duration_sec?.mean != null
                        ? exp.aggregate.duration_sec.mean.toFixed(1)
                        : '—'}
                    </td>
                    <td className={TD} style={{ color: C.fg2 }}>
                      {fmtTokens(exp.aggregate.uncached_input_tokens?.mean != null
                        ? Math.round(exp.aggregate.uncached_input_tokens.mean)
                        : null)}
                    </td>
                    <td
                      className={TD}
                      style={{ color: exp.sample.minimum_met === false ? C.orange : C.fg2 }}
                      title={exp.sample.sample_class ?? undefined}
                    >
                      {fmtPercent(exp.sample.sample_adequacy)}
                    </td>
                    <td className={TD}>
                      <FindingChips counts={findingCounts(exp)} />
                    </td>
                  </tr>
                  {isOpen && (
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                      <td colSpan={9} className="p-0">
                        <ExperimentExpansion exp={exp} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ExperimentsPage() {
  const experiments = useQuery({ queryKey: ['experiments'], queryFn: api.experiments });

  const groups = useMemo(() => {
    const map = new Map<string, ExperimentRecord[]>();
    for (const exp of experiments.data?.experiments ?? []) {
      const key = `${exp.scenario ?? 'unknown'}:${exp.revision ?? 'unknown'}`;
      const list = map.get(key) ?? [];
      list.push(exp);
      map.set(key, list);
    }
    return [...map.entries()];
  }, [experiments.data]);

  if (experiments.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs" style={{ color: C.fg1 }}>
        Loading experiments…
      </div>
    );
  }
  if (experiments.isError) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs" style={{ color: C.red }}>
        Failed to load experiments: {(experiments.error as Error).message}
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
        <span className="text-sm" style={{ color: C.fg2 }}>
          No experiments to compare yet
        </span>
        <span className="max-w-md text-xs leading-relaxed" style={{ color: C.fg0 }}>
          Generate benchmark data first, then rebuild the review dataset:
        </span>
        <code
          className="num rounded px-2.5 py-1.5 text-[11px]"
          style={{ color: C.fg3, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
        >
          make benchmark-fixture-synthetic && make review-surface-data
        </code>
      </div>
    );
  }

  return (
    <div className="sb flex flex-1 flex-col gap-4 overflow-auto p-4">
      {groups.map(([key, exps]) => (
        <ExperimentGroup key={key} groupKey={key} experiments={exps} />
      ))}
    </div>
  );
}
