// Run verdict banner: plain-language outcome first, technical detail on demand.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';
import { Badge } from '@/components/Badge';
import { TierPill } from '@/components/Verdict';
import { C } from '@/utils/colors';
import { fmtDuration, fmtScore, fmtTokens } from '@/utils/helpers';
import { runLabel, runSummary, scoreTier } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

function Fact({ label, value, color = C.fg3, title }: { label: string; value: string; color?: string; title?: string }) {
  return (
    <div className="flex flex-col gap-0.5" title={title}>
      <span className="text-[9px] font-medium uppercase tracking-wider" style={{ color: C.fg0 }}>
        {label}
      </span>
      <span className="num text-xs" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

function ArtifactPath({ label, path }: { label: string; path: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="shrink-0 text-[9px] uppercase tracking-wider" style={{ color: C.fg0 }}>
        {label}
      </span>
      <code
        className="num min-w-0 truncate rounded bg-white/5 px-1.5 py-px text-[10px]"
        style={{ color: C.fg2 }}
        title={path}
      >
        {path}
      </code>
      <button
        className="shrink-0 rounded p-0.5 transition hover:bg-white/10"
        title="Copy path"
        onClick={() => {
          void navigator.clipboard.writeText(path);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? (
          <Check className="size-3" style={{ color: C.green }} />
        ) : (
          <Copy className="size-3" style={{ color: C.fg0 }} />
        )}
      </button>
    </div>
  );
}

export function RunHeader({ run, children }: { run: RunRecord; children?: React.ReactNode }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const tier = scoreTier(run.unscored ? null : run.composite_score);
  return (
    <div
      className="flex flex-col gap-2.5 rounded-lg p-3"
      style={{ background: C.surface, border: `1px solid ${C.border}`, borderLeft: `3px solid ${tier.color}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium" style={{ color: C.fg5 }}>
          {run.scenario}@{run.revision} · {runLabel(run.id)}
        </span>
        <TierPill tier={tier} size="lg" detail={`Composite ${fmtScore(run.composite_score)} — ${tier.blurb}`} />
        <span
          className="num rounded px-1.5 py-px text-[10px]"
          style={{ color: C.cyan, background: `${C.cyan}12`, border: `1px solid ${C.cyan}30` }}
        >
          {run.agent_spec}
        </span>
        {run.synthetic && <Badge label="synthetic" />}
        {!run.valid && <Badge label="invalid" color={C.red} title="Run failed validity checks" />}
        <Link
          to={`/#family-${encodeURIComponent(run.scenario)}`}
          className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition hover:bg-white/10"
          style={{ color: C.accent, border: `1px solid ${C.selectedBorder}` }}
          title={`Open the ${run.scenario} comparison in Experiments`}
        >
          Compare agent specs
          <ArrowUpRight className="size-2.5" />
        </Link>
      </div>

      <span className="text-xs leading-relaxed" style={{ color: C.fg3 }}>
        {runSummary(run)}
      </span>

      {children}

      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Fact
          label="score"
          value={fmtScore(run.composite_score)}
          color={tier.color}
          title={`Composite score · quality ${fmtScore(run.quality_score)} · diagnostic ${fmtScore(run.diagnostic_score)}`}
        />
        <Fact label="run time" value={fmtDuration(run.duration_ms)} />
        {run.total_input_tokens === 0 && run.total_output_tokens === 0 ? (
          <Fact label="tokens" value="not recorded" color={C.fg0} title="No token usage captured for this run" />
        ) : (
          <Fact
            label="tokens"
            value={`${fmtTokens(run.total_input_tokens)} in · ${fmtTokens(run.total_output_tokens)} out`}
          />
        )}
        <Fact label="steps" value={String(run.span_count)} title="Projected spans in this run's trace" />
      </div>

      {run.unscored_reasons.length > 0 && (
        <div
          className="flex flex-col gap-1 rounded-md p-2"
          style={{ background: `${C.orange}0d`, border: `1px solid ${C.orange}30` }}
        >
          <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: C.orange }}>
            Why this run is unscored
          </span>
          {run.unscored_reasons.map((reason, i) => (
            <span key={i} className="text-[11px]" style={{ color: C.fg3 }}>
              {reason}
            </span>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-1">
        <button
          className="flex items-center gap-1.5 text-left text-[10px]"
          style={{ color: C.fg0 }}
          onClick={() => setDetailsOpen((open) => !open)}
        >
          {detailsOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
          Technical details — ids, source artifacts, sub-scores
        </button>
        {detailsOpen && (
          <div className="flex flex-col gap-1.5 rounded-md p-2" style={{ background: 'rgba(255,255,255,0.02)' }}>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <Fact label="run id" value={run.id} color={C.fg2} />
              <Fact label="status" value={run.status} color={run.status === 'OK' ? C.green : run.status === 'ERROR' ? C.red : C.fg2} />
              <Fact label="quality" value={fmtScore(run.quality_score)} />
              <Fact label="diagnostic" value={fmtScore(run.diagnostic_score)} />
              <Fact label="valid" value={run.valid ? 'yes' : 'no'} color={run.valid ? C.green : C.red} />
            </div>
            <ArtifactPath label="run json" path={run.artifact_paths.run_json} />
            {run.artifact_paths.findings_json && (
              <ArtifactPath label="findings json" path={run.artifact_paths.findings_json} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
