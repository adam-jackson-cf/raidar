// Run detail → Quality panel (under the scorecard): the run's per-checkpoint
// C·V·E·H·R·G·I·S strip with eligibility badges. A V/E/H/R/G cell or column
// opens its file:line evidence in the full Quality run view. A gated trial also shows its
// commit-gate effort per checkpoint (rounds, failure families, fix cost).
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowUpRight, ChevronDown, ChevronRight } from 'lucide-react';
import { C } from '@/utils/colors';
import { qualityApi } from '@/quality/api';
import { GatePanel } from '@/quality/components/GatePanel';
import { QualityStrip, Ring, StatusBadge } from '@/quality/components/QualityStrip';
import { POLICY_SOURCE_TITLE, fmtComponent } from '@/quality/format';
import { qualityExperimentHref, qualityRunHref, useQualityIndex } from '@/quality/links';
import type { RunRecord } from '@/utils/types';
import '@/quality/quality.css';

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <section
      aria-label="Quality"
      data-testid="run-quality-panel"
      className="flex flex-col gap-1.5 rounded-lg p-2.5"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      {children}
    </section>
  );
}

export function RunQualityPanel({ run }: { run: RunRecord }) {
  const index = useQualityIndex();
  const navigate = useNavigate();
  const [open, setOpen] = useState(true);
  const quality = index.runFor(run.artifact_paths.run_json);
  const detail = useQuery({
    queryKey: ['quality-run', quality?.id],
    queryFn: () => qualityApi.run(quality!.id),
    enabled: quality != null,
  });

  if (!index.loaded) return null;
  if (!quality) {
    // Projected for review, but not in the Quality data: say how to add it.
    return (
      <Shell>
        <span className="flex items-center gap-2 text-xs">
          <span className="font-medium" style={{ color: C.fg3 }}>
            Quality
          </span>
          <span className="text-[10px]" style={{ color: C.fg0 }}>
            Not assessed yet. Rescore this trial's job with code-quality@1 and correctness@1, then run make
            review-surface-quality-data.
          </span>
        </span>
      </Shell>
    );
  }

  const data = detail.data;
  const target = index.targetOf(quality);
  return (
    <Shell>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="q-focus flex items-center gap-2 text-left"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? (
            <ChevronDown className="size-3.5" style={{ color: C.fg0 }} />
          ) : (
            <ChevronRight className="size-3.5" style={{ color: C.fg0 }} />
          )}
          <span className="text-xs font-medium" style={{ color: C.fg3 }}>
            Quality
          </span>
        </button>
        <span className="inline-flex items-center gap-1.5" data-testid="run-quality-score" title="Headline S for this problem">
          {quality.score.status === 'ok' ? (
            <>
              <Ring component="S" cell={quality.score} />
              <span className="num text-[11px] font-bold" style={{ color: C.fg4 }}>
                S {fmtComponent('S', quality.score.value)}
              </span>
            </>
          ) : (
            <StatusBadge cell={quality.score} />
          )}
        </span>
        {data && (
          <span className="text-[10px]" style={{ color: C.fg1 }} title={POLICY_SOURCE_TITLE[data.policy.source]}>
            {data.policy.ref}
          </span>
        )}
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          code quality per checkpoint — pick a V, E, H, R or G cell for its evidence
        </span>
        <span className="ml-auto flex items-center gap-3 text-[10px]">
          <Link
            to={qualityExperimentHref(target)}
            className="q-focus underline-offset-2 hover:underline"
            style={{ color: C.fg2 }}
            title={`Compare arms on ${target.problem} in ${target.experiment}`}
          >
            Compare arms
          </Link>
          <Link
            to={qualityRunHref(quality.id)}
            className="q-focus inline-flex items-center gap-0.5 underline-offset-2 hover:underline"
            style={{ color: C.accent }}
          >
            Full quality view
            <ArrowUpRight className="size-3" aria-hidden />
          </Link>
        </span>
      </div>
      {open && (
        <>
          {detail.isLoading && (
            <span className="px-1 text-[10px]" style={{ color: C.fg1 }}>
              Loading quality…
            </span>
          )}
          {detail.isError && (
            <span className="px-1 text-[10px]" style={{ color: C.red }}>
              Quality detail could not be loaded. Run make review-surface-quality-data, then reload.
            </span>
          )}
          {data && (
            <>
              <QualityStrip
                rows={data.rows}
                problem={data.problem}
                policyRef={data.policy.ref}
                selection={null}
                onSelect={(selection) => navigate(qualityRunHref(quality.id, selection))}
              />
              {data.policy_reasons.length > 0 && (
                <ul className="px-2 text-[10px]" style={{ color: C.fg2 }} data-testid="policy-reasons">
                  {data.policy_reasons.map((reason) => (
                    <li key={reason}>Headline S not eligible: {reason}</li>
                  ))}
                </ul>
              )}
              {data.notes.map((note) => (
                <p key={note} className="px-2 text-[10px]" style={{ color: C.orange }}>
                  {note}
                </p>
              ))}
              <div className="px-1 pt-1">
                <GatePanel gate={data.gate} />
              </div>
            </>
          )}
        </>
      )}
    </Shell>
  );
}
