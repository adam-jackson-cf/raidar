// Quality element, experiment view: per problem, the arms' component curves,
// paired Δ vs baseline, SCB-dashboard distributions, the head-to-head
// checkpoint scatter and score sensitivity, then the trials to drill into.
import { useEffect, useMemo, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/Badge';
import { FamilyPolicyMismatch } from '@/components/PolicyMismatchNotice';
import { C } from '@/utils/colors';
import { qualityApi } from '@/quality/api';
import { ArmLegend, ComponentCurves } from '@/quality/components/ComponentCurves';
import { Distributions } from '@/quality/components/Distributions';
import { HeadToHeadScatter } from '@/quality/components/HeadToHeadScatter';
import { PairedDeltaTable } from '@/quality/components/PairedDeltaTable';
import { SensitivityPanel } from '@/quality/components/SensitivityPanel';
import { StatusBadge } from '@/quality/components/QualityStrip';
import { POLICY_SOURCE_TITLE, experimentKey, experimentLabel, fmtComponent } from '@/quality/format';
import { matchesTarget } from '@/quality/links';
import type { ProblemQuality, QualityExperiment, QualityRunSummary } from '@/quality/types';
import '@/quality/quality.css';

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId: string }) {
  return (
    <section className="py-4" style={{ borderTop: `1px solid ${C.border}` }} data-testid={testId} aria-label={title}>
      <h3 className="mb-2 text-xs font-semibold" style={{ color: C.fg4 }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

function TrialList({ runs }: { runs: QualityRunSummary[] }) {
  return (
    <table className="w-full border-collapse text-left" data-testid="quality-trials">
      <thead>
        <tr style={{ borderBottom: `1px solid ${C.border}` }}>
          {['Trial', 'Arm', 'Attempt', 'Job', 'S'].map((heading) => (
            <th key={heading} className="px-2 py-1 text-[10px] font-medium" style={{ color: C.fg1 }}>
              {heading}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id} style={{ borderBottom: `1px solid ${C.rowBorder}` }}>
            <td className="px-2 py-1 text-[11px]">
              <Link to={`/quality/runs/${encodeURIComponent(run.id)}`} className="q-focus underline-offset-2 hover:underline" style={{ color: C.accent }}>
                {run.trial}
              </Link>
              {!run.valid && <span className="ml-2 text-[10px]" style={{ color: C.orange }}>invalid</span>}
              {run.truncated_to != null && (
                <span className="ml-2 text-[10px]" style={{ color: C.orange }}>
                  truncated to {run.truncated_to}
                </span>
              )}
            </td>
            <td className="px-2 py-1 text-[11px]" style={{ color: C.fg2 }}>
              {run.arm}
            </td>
            <td className="num px-2 py-1 text-[11px]" style={{ color: C.fg2 }}>
              {run.attempt}
            </td>
            <td className="px-2 py-1 text-[11px]" style={{ color: C.fg1 }}>
              {run.job}
            </td>
            <td className="px-2 py-1 text-[11px]" style={{ color: C.fg3 }}>
              {run.score.status === 'ok' ? fmtComponent('S', run.score.value) : <StatusBadge cell={run.score} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ProblemView({
  problem,
  runs,
  experiments,
  focused,
}: {
  problem: ProblemQuality;
  runs: QualityRunSummary[];
  experiments: QualityExperiment[];
  focused: boolean;
}) {
  const members = runs.filter((run) => problem.run_ids.includes(run.id));
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'start' });
  }, [focused]);
  return (
    <article
      ref={ref}
      className="mb-8 scroll-mt-2"
      data-testid="quality-problem"
      data-focused={focused || undefined}
      aria-label={problem.problem}
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 pb-2">
        <h2 className="text-sm font-semibold" style={{ color: C.fg5 }}>
          {problem.problem}
        </h2>
        <span className="text-[11px]" style={{ color: C.fg1 }} title={POLICY_SOURCE_TITLE[problem.policy.source]}>
          {problem.policy.ref}
        </span>
        <span className="text-[11px]" style={{ color: C.fg1 }}>
          {problem.checkpoints.length} checkpoints
        </span>
        <span className="ml-auto">
          <ArmLegend arms={problem.arms} baseline={problem.baseline_arm} />
        </span>
      </header>
      <div className="mb-2">
        <FamilyPolicyMismatch problem={problem.problem} quality={experiments} />
      </div>
      {(problem.notes.length > 0 || problem.excluded.length > 0) && (
        <ul className="mb-2 text-[10px]" style={{ color: C.orange }} data-testid="quality-exclusions">
          {problem.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
          {problem.excluded.map((item) => (
            <li key={item} style={{ color: C.fg2 }}>
              Left out: {item}
            </li>
          ))}
        </ul>
      )}
      <Section title="Component curves" testId="quality-curves">
        <ComponentCurves arms={problem.arms} curves={problem.curves} checkpoints={problem.checkpoints} />
      </Section>
      <Section title={`Paired Δ vs ${problem.baseline_arm ?? 'baseline'}`} testId="quality-paired">
        <PairedDeltaTable paired={problem.paired} baseline={problem.baseline_arm} arms={problem.arms} />
      </Section>
      <Section title="Distributions" testId="quality-distributions">
        <Distributions distributions={problem.distributions} arms={problem.arms} />
      </Section>
      <Section title="Head-to-head by checkpoint" testId="quality-h2h">
        <HeadToHeadScatter headToHead={problem.head_to_head} />
      </Section>
      <Section title="Score sensitivity" testId="quality-sensitivity">
        <SensitivityPanel sensitivity={problem.sensitivity} />
      </Section>
      <Section title="Trials" testId="quality-trial-section">
        <TrialList runs={members} />
      </Section>
    </article>
  );
}

// The pre-registered primary outcome (analysis.primary_outcome in the lock, else the
// progression's default), labelled as the analysis report labels it; an errata-applied
// primary names the raw SCB outcome reported beside it.
function PrimaryOutcome({ experiment }: { experiment: QualityExperiment }) {
  if (!experiment.primary_outcome) return null;
  return (
    <p data-testid="primary-outcome" className="mb-3 text-xs" style={{ color: C.fg2 }}>
      Primary outcome:{' '}
      <code style={{ color: C.fg4 }}>{experiment.primary_outcome}</code>
      {experiment.primary_source && <span style={{ color: C.fg1 }}> ({experiment.primary_source})</span>}
      {experiment.primary_raw && (
        <span data-testid="primary-raw">
          {' '}
          · raw <code style={{ color: C.fg4 }}>{experiment.primary_raw}</code> reported beside it
        </span>
      )}
    </p>
  );
}

export function QualityPage() {
  const experiments = useQuery({ queryKey: ['quality-experiments'], queryFn: qualityApi.experiments });
  const runs = useQuery({ queryKey: ['quality-runs'], queryFn: qualityApi.runs });
  // Deep links (?experiment=&lock=&problem=) come from the comparison views and run detail.
  const [params, setParams] = useSearchParams();
  const list = useMemo(() => experiments.data?.experiments ?? [], [experiments.data]);
  const focusProblem = params.get('problem');
  const current =
    list.find((item) => matchesTarget(item, params.get('experiment'), params.get('lock'))) ?? list[0];
  const select = (key: string) => {
    const item = list.find((entry) => experimentKey(entry) === key);
    if (!item) return;
    const next = new URLSearchParams({ experiment: item.experiment });
    if (item.lock_sha256) next.set('lock', item.lock_sha256.slice(0, 12));
    setParams(next, { replace: true });
  };

  let body: React.ReactNode;
  if (experiments.isLoading || runs.isLoading) {
    body = <p className="text-xs" style={{ color: C.fg1 }}>Loading quality data…</p>;
  } else if (experiments.isError || runs.isError) {
    body = (
      <p className="text-xs" style={{ color: C.red }}>
        Quality data could not be loaded. Run make review-surface-quality-data, then reload.
      </p>
    );
  } else if (!current) {
    body = (
      <p className="text-xs" style={{ color: C.fg1 }}>
        No assessed trials yet. Rescore a job with code-quality@1 and correctness@1, then run make review-surface-quality-data.
      </p>
    );
  } else {
    body = (
      <>
        <PrimaryOutcome experiment={current} />
        {current.problems.map((problem) => (
          <ProblemView
            key={problem.problem}
            problem={problem}
            runs={runs.data?.runs ?? []}
            experiments={list}
            focused={problem.problem === focusProblem}
          />
        ))}
      </>
    );
  }

  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-6 py-4" data-testid="quality-page">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-sm font-semibold" style={{ color: C.fg5 }}>
          Quality
        </h1>
        {experiments.data?.synthetic && <Badge label="Synthetic" title="Synthetic fixture data, not benchmark evidence" />}
        {list.length > 0 && (
          <select
            aria-label="Experiment"
            className="q-focus ml-auto rounded px-2 py-1 text-xs"
            style={{ background: C.input, color: C.fg3, border: `1px solid ${C.borderLight}` }}
            value={current ? experimentKey(current) : ''}
            onChange={(event) => select(event.target.value)}
          >
            {list.map((item) => (
              <option key={experimentKey(item)} value={experimentKey(item)}>
                {experimentLabel(item)}
              </option>
            ))}
          </select>
        )}
      </div>
      {body}
    </main>
  );
}
