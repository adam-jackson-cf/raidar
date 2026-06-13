import { Fragment, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronRight, Trophy } from 'lucide-react';
import { api } from '@/api/client';
import { KIND_STYLES } from '@/components/AnnotationChip';
import { EvidenceRefList } from '@/components/AnnotationCards';
import { Badge } from '@/components/Badge';
import { FailurePatterns } from '@/components/FailurePatterns';
import { FindingChips } from '@/components/FindingChips';
import { RevisionMovement } from '@/components/RevisionMovement';
import { TradeoffScatter } from '@/components/TradeoffScatter';
import { ScoreBar, ScoreVerdict, TierPill } from '@/components/Verdict';
import { C } from '@/utils/colors';
import { fmtPercent, fmtScore, fmtTokens } from '@/utils/helpers';
import {
  categoryLabel,
  humanize,
  runLabel,
  sampleTrust,
  scoreTier,
  scorerName,
  spreadTier,
} from '@/utils/verdict';
import type { AnnotationKind, ExperimentRecord, RevisionDiff, RunRecord, StatBlock } from '@/utils/types';

function statDetail(stat: StatBlock | undefined): string {
  if (stat?.mean == null) return 'No scored runs';
  const parts = [`mean ${stat.mean.toFixed(3)}`];
  if (stat.stddev != null) parts.push(`±${stat.stddev.toFixed(3)}`);
  if (stat.median != null) parts.push(`median ${stat.median.toFixed(3)}`);
  if (stat.min != null && stat.max != null) parts.push(`range ${stat.min.toFixed(3)}–${stat.max.toFixed(3)}`);
  return parts.join(' · ');
}

/** Findings across the experiment AND its runs — the table answers "is anything wrong here?", so run-level issues must count. */
function findingCounts(exp: ExperimentRecord, runsById: Map<string, RunRecord>): Partial<Record<AnnotationKind, number>> {
  const counts: Partial<Record<AnnotationKind, number>> = {};
  for (const f of exp.findings) counts[f.kind] = (counts[f.kind] ?? 0) + 1;
  for (const id of exp.run_ids) {
    const run = runsById.get(id);
    if (!run) continue;
    for (const kind of ['issue', 'good', 'note'] as const) {
      counts[kind] = (counts[kind] ?? 0) + run.finding_counts[kind];
    }
  }
  return counts;
}

function issueCount(exp: ExperimentRecord, runsById: Map<string, RunRecord>): number {
  return findingCounts(exp, runsById).issue ?? 0;
}

const TH = 'px-2.5 py-1.5 text-left text-[9px] font-medium uppercase tracking-wider';
const TD = 'px-2.5 py-2 text-[11px] align-middle';

function RunPill({ run, id }: { run: RunRecord | undefined; id: string }) {
  const tier = scoreTier(run?.unscored ? null : (run?.composite_score ?? null));
  const failed = run?.status === 'ERROR';
  return (
    <Link
      to={`/runs/${encodeURIComponent(id)}`}
      title={`${id}\n${tier.label}${run?.composite_score != null ? ` · composite ${fmtScore(run.composite_score)}` : ''}${failed ? ' · run errored' : ''} — open run review`}
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] transition hover:bg-white/10"
      style={{ color: failed ? C.red : C.fg3, border: `1px solid ${failed ? 'rgba(235,20,20,0.35)' : C.border}` }}
    >
      <span className="inline-block size-1.5 rounded-full" style={{ background: tier.color }} />
      {runLabel(id)}
      {run?.composite_score != null && (
        <span className="num" style={{ color: tier.color }}>
          {run.composite_score.toFixed(2)}
        </span>
      )}
      {run && <FindingChips counts={run.finding_counts} />}
    </Link>
  );
}

function MetricOutcomeRow({
  metric,
  outcome,
}: {
  metric: string;
  outcome: { pass_rate: number; mean_score: number; sample_size: number; pass_count: number; fail_count: number };
}) {
  const failing = outcome.pass_rate < 1;
  return (
    <div
      className="flex items-center gap-2 rounded px-1.5 py-1"
      title={`${metric} · pass rate ${fmtPercent(outcome.pass_rate)} · mean score ${fmtScore(outcome.mean_score)} · ${outcome.sample_size} samples`}
    >
      <span className="w-3 shrink-0 text-center text-[10px] font-bold" style={{ color: failing ? C.red : C.green }}>
        {failing ? '✗' : '✓'}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11px]" style={{ color: C.fg3 }}>
        {humanize(metric)}
      </span>
      <ScoreBar score={outcome.mean_score} width={56} color={failing ? C.red : C.green} />
      <span className="num w-20 shrink-0 text-right text-[10px]" style={{ color: failing ? C.orange : C.fg1 }}>
        {failing
          ? `${outcome.pass_count}/${outcome.sample_size} runs pass`
          : `${outcome.sample_size}/${outcome.sample_size} pass`}
      </span>
    </div>
  );
}

function ExperimentExpansion({ exp }: { exp: ExperimentRecord }) {
  const metricOutcomes = Object.entries(exp.aggregate.metric_outcomes ?? {});
  const scorerOutcomes = Object.entries(exp.aggregate.scorer_outcomes ?? {});
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const runsById = new Map((runs.data ?? []).map((run) => [run.id, run]));
  const failing = metricOutcomes.filter(([, o]) => o.pass_rate < 1).sort(([, a], [, b]) => a.pass_rate - b.pass_rate);
  const passing = metricOutcomes.filter(([, o]) => o.pass_rate >= 1);
  return (
    <div className="flex flex-col gap-3 px-3 py-2.5" style={{ background: 'rgba(255,255,255,0.015)' }}>
      {exp.run_ids.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Runs — open one to see its full story
          </div>
          <div className="flex flex-wrap gap-1.5">
            {exp.run_ids.map((id) => (
              <RunPill key={id} run={runsById.get(id)} id={id} />
            ))}
          </div>
        </div>
      )}

      {metricOutcomes.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: failing.length > 0 ? C.orange : C.fg1 }}>
              {failing.length > 0 ? `Where points were lost (${failing.length})` : 'Where points were lost'}
            </div>
            {failing.length === 0 ? (
              <span className="text-[11px]" style={{ color: C.fg1 }}>
                Nothing — every check passed in every scored run.
              </span>
            ) : (
              <div className="flex max-w-md flex-col">
                {failing.map(([metric, o]) => (
                  <MetricOutcomeRow key={metric} metric={metric} outcome={o} />
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
              What held up ({passing.length})
            </div>
            <div className="flex max-w-md flex-col">
              {passing.map(([metric, o]) => (
                <MetricOutcomeRow key={metric} metric={metric} outcome={o} />
              ))}
            </div>
          </div>
        </div>
      )}

      {scorerOutcomes.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
            Score areas
          </div>
          <div className="flex flex-wrap gap-1.5">
            {scorerOutcomes.map(([scorer, o]) => (
              <span
                key={scorer}
                title={`${scorer} · mean score ${fmtScore(o.mean_score)} across ${o.sample_size} runs`}
                className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px]"
                style={{ color: C.fg2, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
              >
                {scorerName(scorer)}
                <ScoreBar score={o.mean_score} width={40} />
                <span className="num" style={{ color: scoreTier(o.mean_score).color }}>{fmtScore(o.mean_score)}</span>
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
                      <span className="text-[10px] font-medium" style={{ color: C.fg1 }} title={f.category}>
                        {categoryLabel(f.category)}
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
    </div>
  );
}

/** One plain-language sentence answering "who wins this scenario revision, and what should I look at first?" */
function GroupHeadline({ sorted, runsById }: { sorted: ExperimentRecord[]; runsById: Map<string, RunRecord> }) {
  const best = sorted[0];
  const bestMean = best?.aggregate.composite_score?.mean ?? null;
  if (best == null || bestMean == null) {
    return (
      <span className="text-[11px]" style={{ color: C.fg1 }}>
        No scored runs yet — rerun the benchmark to get a verdict.
      </span>
    );
  }
  const bestTier = scoreTier(bestMean);
  const runnerUp = sorted.length > 1 ? sorted[1] : null;
  const runnerMean = runnerUp?.aggregate.composite_score?.mean ?? null;

  const worstRun = sorted
    .flatMap((exp) => exp.run_ids.map((id) => runsById.get(id)))
    .filter((run): run is RunRecord => run != null)
    .sort((a, b) => (b.finding_counts.issue - a.finding_counts.issue) || ((a.composite_score ?? 1) - (b.composite_score ?? 1)))[0];
  const hasTrouble = worstRun != null && worstRun.finding_counts.issue > 0;

  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-relaxed" style={{ color: C.fg3 }}>
      <TierPill tier={bestTier} />
      <span>
        {sorted.length > 1 ? 'Best delivery: ' : ''}
        <span style={{ color: C.fg4 }}>{best.agent_spec}</span>
        {' — '}
        {bestTier.blurb.toLowerCase()} ({bestMean.toFixed(2)})
        {runnerUp && runnerMean != null && (
          <>
            {'. '}
            <span style={{ color: C.fg4 }}>{runnerUp.agent_spec}</span>
            {` trails by ${(bestMean - runnerMean).toFixed(2)}`}
            {issueCount(runnerUp, runsById) > 0 &&
              ` with ${issueCount(runnerUp, runsById)} issue${issueCount(runnerUp, runsById) === 1 ? '' : 's'}`}
          </>
        )}
        {'.'}
      </span>
      {hasTrouble && (
        <span>
          Start with{' '}
          <Link
            to={`/runs/${encodeURIComponent(worstRun.id)}`}
            className="underline decoration-dotted underline-offset-2 transition hover:opacity-80"
            style={{ color: C.accent }}
            title={`${worstRun.id} — ${worstRun.finding_counts.issue} issues`}
          >
            {runLabel(worstRun.id)}
          </Link>
          {worstRun.status === 'ERROR' ? ' (the failing run)' : ' (most issues)'}.
        </span>
      )}
    </span>
  );
}

function ExperimentGroup({ groupKey, experiments }: { groupKey: string; experiments: ExperimentRecord[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const runsById = useMemo(() => new Map((runs.data ?? []).map((run) => [run.id, run])), [runs.data]);

  const sorted = useMemo(
    () =>
      [...experiments].sort(
        (a, b) =>
          (b.aggregate.composite_score?.mean ?? -1) - (a.aggregate.composite_score?.mean ?? -1),
      ),
    [experiments],
  );

  const bestComposite = sorted[0]?.aggregate.composite_score?.mean ?? null;
  const meta = experiments.find((e) => e.scenario_meta)?.scenario_meta ?? null;

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
      <div
        className="flex flex-col gap-1.5 px-3 py-2.5"
        style={{ borderBottom: `1px solid ${C.border}` }}
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium" style={{ color: C.fg5 }}>
            {groupKey}
          </span>
          {meta?.category && (
            <span
              className="rounded px-1.5 py-px text-[9px] font-medium uppercase tracking-wide"
              style={{ color: C.fg1, background: 'rgba(255,255,255,0.05)' }}
            >
              {meta.category}
            </span>
          )}
          {meta?.difficulty && (
            <span
              className="rounded px-1.5 py-px text-[9px] font-medium uppercase tracking-wide"
              style={{ color: C.fg1, background: 'rgba(255,255,255,0.05)' }}
            >
              {meta.difficulty}
            </span>
          )}
          <span className="text-[10px]" style={{ color: C.fg0 }}>
            {experiments.length} agent spec{experiments.length === 1 ? '' : 's'}
          </span>
        </div>
        {meta?.description && (
          <span className="text-[11px]" style={{ color: C.fg1 }}>
            {meta.description}
          </span>
        )}
        <GroupHeadline sorted={sorted} runsById={runsById} />
      </div>
      <div className="sb overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th className={TH} style={{ color: C.fg0 }}>Agent spec</th>
              <th className={TH} style={{ color: C.fg0 }}>Delivery</th>
              <th className={TH} style={{ color: C.fg0 }}>Repeatability</th>
              <th className={TH} style={{ color: C.fg0 }}>Issues</th>
              <th className={TH} style={{ color: C.fg0 }} title="Can you trust this sample? Based on scored-run counts vs the scenario's minimum and preferred sample sizes.">
                Confidence
              </th>
              <th className={TH} style={{ color: C.fg0 }}>Pace</th>
              <th className={TH} style={{ color: C.fg0 }}>Tokens</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((exp) => {
              const isOpen = expanded.has(exp.experiment_id);
              const compositeMean = exp.aggregate.composite_score?.mean ?? null;
              const isBest =
                bestComposite != null && compositeMean != null && compositeMean === bestComposite;
              const delta =
                bestComposite != null && compositeMean != null ? compositeMean - bestComposite : null;
              const scored = exp.aggregate.run_count_scored ?? 0;
              const total = exp.aggregate.run_count_total ?? 0;
              const unscored = exp.aggregate.unscored_count ?? 0;
              const trust = sampleTrust(exp.sample, scored, total);
              const spread = spreadTier(exp.aggregate.composite_score);
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
                      <span className="flex items-center gap-1.5" title={exp.experiment_id}>
                        <ChevronRight
                          className="size-3 shrink-0 transition-transform"
                          style={{ color: C.fg1, transform: isOpen ? 'rotate(90deg)' : '' }}
                        />
                        <span className="num" style={{ color: C.cyan }}>{exp.agent_spec}</span>
                        {isBest && sorted.length > 1 && (
                          <Trophy className="size-3" style={{ color: C.accent }} aria-label="Best composite mean" />
                        )}
                        {exp.synthetic && <Badge label="synthetic" />}
                      </span>
                    </td>
                    <td className={TD}>
                      <span className="flex items-center gap-2">
                        <ScoreVerdict
                          score={compositeMean}
                          detail={statDetail(exp.aggregate.composite_score)}
                        />
                        {delta != null && delta < 0 && (
                          <span className="num text-[10px]" style={{ color: C.orange }} title="Gap to the best agent spec in this group">
                            {delta.toFixed(2)} vs best
                          </span>
                        )}
                      </span>
                    </td>
                    <td className={TD}>
                      {spread ? (
                        <span title={`${spread.blurb} (±${exp.aggregate.composite_score?.stddev?.toFixed(3)})`} style={{ color: spread.color }}>
                          {spread.label}
                        </span>
                      ) : (
                        <span style={{ color: C.fg0 }}>—</span>
                      )}
                    </td>
                    <td className={TD}>
                      {(() => {
                        const counts = findingCounts(exp, runsById);
                        const issues = counts.issue ?? 0;
                        const positives = (counts.good ?? 0) + (counts.note ?? 0);
                        return (
                          <span
                            className="flex items-center gap-1.5"
                            title={`${issues} issue${issues === 1 ? '' : 's'}, ${counts.good ?? 0} good, ${counts.note ?? 0} note across this spec's runs and experiment checks`}
                          >
                            {issues > 0 ? (
                              <span
                                className="num inline-flex h-[18px] items-center gap-1 rounded-full px-1.5 text-[10px] font-medium leading-[18px]"
                                style={{ color: '#f87171', background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.35)' }}
                              >
                                <span className="font-bold">!</span>
                                {issues}
                              </span>
                            ) : (
                              <span style={{ color: C.green }}>none</span>
                            )}
                            {positives > 0 && (
                              <span className="text-[10px]" style={{ color: C.fg0 }}>
                                · {positives} positive
                              </span>
                            )}
                          </span>
                        );
                      })()}
                    </td>
                    <td className={TD}>
                      <span className="flex items-center gap-1.5">
                        <TierPill tier={trust} />
                        {unscored > 0 && (
                          <span className="num text-[10px]" style={{ color: C.orange }} title={`${unscored} unscored run(s) need rerun`}>
                            {unscored} unscored
                          </span>
                        )}
                      </span>
                    </td>
                    <td className={`num ${TD}`} style={{ color: C.fg2 }} title={statDetail(exp.aggregate.duration_sec)}>
                      {exp.aggregate.duration_sec?.mean != null
                        ? `${(exp.aggregate.duration_sec.mean / 60).toFixed(1)}m avg`
                        : '—'}
                    </td>
                    <td className={`num ${TD}`} style={{ color: C.fg2 }} title={statDetail(exp.aggregate.uncached_input_tokens)}>
                      {exp.aggregate.uncached_input_tokens?.mean != null
                        ? `${fmtTokens(Math.round(exp.aggregate.uncached_input_tokens.mean))} avg`
                        : '—'}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                      <td colSpan={7} className="p-0">
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

  const families = useMemo(() => {
    const map = new Map<string, Map<string, ExperimentRecord[]>>();
    for (const exp of experiments.data?.experiments ?? []) {
      const family = exp.scenario ?? 'unknown';
      const revision = exp.revision ?? 'unknown';
      const revisions = map.get(family) ?? new Map<string, ExperimentRecord[]>();
      const list = revisions.get(revision) ?? [];
      list.push(exp);
      revisions.set(revision, list);
      map.set(family, revisions);
    }
    return [...map.entries()].map(([family, revisions]) => ({
      family,
      revisions: [...revisions.entries()].sort(([a], [b]) => a.localeCompare(b)),
      all: [...revisions.values()].flat(),
    }));
  }, [experiments.data]);

  const groups = families;

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

  const revisionDiffs = experiments.data?.revision_diffs ?? [];

  return (
    <div className="sb flex flex-1 flex-col gap-5 overflow-auto p-4">
      {families.length > 1 && (
        <nav className="flex flex-wrap items-center gap-1.5" aria-label="Scenario families">
          <span className="text-[10px] uppercase tracking-wider" style={{ color: C.fg0 }}>
            scenarios
          </span>
          {families.map(({ family }) => (
            <a
              key={family}
              href={`#family-${family}`}
              className="num rounded px-1.5 py-0.5 text-[10px] transition hover:bg-white/10"
              style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
            >
              {family}
            </a>
          ))}
        </nav>
      )}
      {families.map(({ family, revisions, all }) => (
        <FamilySection
          key={family}
          family={family}
          revisions={revisions}
          all={all}
          diffs={revisionDiffs.filter((diff) => diff.scenario === family)}
        />
      ))}
    </div>
  );
}

function FamilySection({
  family,
  revisions,
  all,
  diffs,
}: {
  family: string;
  revisions: Array<[string, ExperimentRecord[]]>;
  all: ExperimentRecord[];
  diffs: RevisionDiff[];
}) {
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const familyRunIds = new Set(all.flatMap((exp) => exp.run_ids));
  const familyRuns = (runs.data ?? []).filter((run) => familyRunIds.has(run.id));
  return (
    <div id={`family-${family}`} className="flex scroll-mt-4 flex-col gap-3">
      {revisions.map(([revision, exps]) => (
        <ExperimentGroup key={`${family}:${revision}`} groupKey={`${family}:${revision}`} experiments={exps} />
      ))}
      <div className="grid items-start gap-3 xl:grid-cols-2">
        <TradeoffScatter runs={familyRuns} />
        <FailurePatterns runs={familyRuns} />
      </div>
      <RevisionMovement experiments={all} diffs={diffs} />
    </div>
  );
}
