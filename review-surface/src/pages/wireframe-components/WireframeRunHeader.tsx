import { useState, type MouseEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Check, ChevronDown, ChevronRight, Copy, MessageSquarePlus } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtDuration, fmtScore, fmtTokens } from '@/utils/helpers';
import { runLabel, runSummary, scoreTier } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

type HoverPayload = {
  x: number;
  y: number;
  title: string;
  lines: string[];
};

const FACT_HELPERS: Record<string, string[]> = {
  score: [
    'Composite outcome score for this run.',
    'This is the primary quality signal used when comparing runs.',
  ],
  tokens: [
    'Input and output token usage captured for this run.',
    'Use this to understand spend and verbosity pressure.',
  ],
  'run time': [
    'Total elapsed runtime for this run.',
    'Useful for comparing speed against outcome and token spend.',
  ],
  steps: [
    "Projected span count in this run's trace.",
    'A higher count usually means a more involved execution path.',
  ],
  diagnostic: [
    'Supporting scorer health signal for this run.',
    'Formed from retained diagnostic scoring output, not from the primary outcome calculation.',
    'Use it to understand evidence quality or execution health when outcome and trace status disagree.',
  ],
  status: [
    'Execution status for the run trace.',
    'OK means the run completed; ERROR indicates execution failed.',
  ],
  valid: [
    'Whether this run passed validity checks and can be trusted for review.',
    'Invalid runs may be unscored or excluded from comparison.',
  ],
};

type UnscoredReasonView = {
  id: string;
  label: string;
  stage: string;
  detail: string;
  action?: string;
};

const UNSCORED_REASON_COPY: Record<string, { label: string; stage: string; detail: string; action: string }> = {
  harbor_timeout: {
    label: 'Harness timeout',
    stage: 'execution',
    detail: 'The harness exceeded its allowed execution window before scoring could complete.',
    action: 'Re-run this scenario, or increase the timeout if this is expected for the agent spec.',
  },
  compose_version_unsupported: {
    label: 'Compose unsupported',
    stage: 'environment',
    detail: 'The local Docker Compose version is not supported by the harness.',
    action: 'Update the local harness environment, then re-run this scenario.',
  },
  provider_rate_limit: {
    label: 'Provider rate limit',
    stage: 'provider',
    detail: 'The model provider rate-limited the run before scoring could complete.',
    action: 'Re-run after the provider limit clears.',
  },
  provider_stream_disconnect: {
    label: 'Provider disconnect',
    stage: 'provider',
    detail: 'The model provider stream disconnected before the run completed.',
    action: 'Re-run this scenario to obtain a complete scored run.',
  },
  harness_unavailable: {
    label: 'Harness unavailable',
    stage: 'environment',
    detail: 'The configured harness was unavailable when the run executed.',
    action: 'Restore the harness environment, then re-run this scenario.',
  },
  harbor_cli_failure: {
    label: 'Harness CLI failure',
    stage: 'execution',
    detail: 'The Harbor CLI returned a failure before scoring could complete.',
    action: 'Inspect the trace for the failed command, fix the harness issue, then re-run.',
  },
  harbor_trial_exception: {
    label: 'Harness trial exception',
    stage: 'execution',
    detail: 'The harness trial raised an exception before scoring could complete.',
    action: 'Inspect the trace output, resolve the harness exception, then re-run.',
  },
  provider_or_harness_turn_failure: {
    label: 'Turn failure',
    stage: 'execution',
    detail: 'The provider or harness failed during an agent turn before scoring could complete.',
    action: 'Inspect the trace for the failing turn, then re-run this scenario.',
  },
};

function fallbackUnscoredReason(reason: string) {
  const normalized = reason.toLowerCase();
  if (normalized.includes('verification')) {
    return {
      label: 'Scoring incomplete',
      stage: 'verification',
      detail: reason,
      action: 'Re-run this scenario to obtain a score.',
    };
  }
  if (normalized.includes('rate limit')) {
    return UNSCORED_REASON_COPY.provider_rate_limit;
  }
  if (normalized.includes('timeout')) {
    return UNSCORED_REASON_COPY.harbor_timeout;
  }
  if (normalized.includes('harness')) {
    return {
      label: 'Harness interruption',
      stage: 'execution',
      detail: reason,
      action: 'Inspect the trace, resolve the harness interruption, then re-run.',
    };
  }
  return {
    label: 'Run could not be scored',
    stage: 'scoring',
    detail: reason,
    action: 'Inspect the trace and re-run this scenario after the underlying issue is resolved.',
  };
}

function unscoredReasonViews(reasons: string[]): UnscoredReasonView[] {
  const views: UnscoredReasonView[] = [];
  for (const reason of reasons) {
    if (/^re-?run\b/i.test(reason) && views.length > 0) {
      views[views.length - 1].action = reason;
      continue;
    }
    const copy = UNSCORED_REASON_COPY[reason] ?? fallbackUnscoredReason(reason);
    views.push({
      id: `${reason}-${views.length}`,
      label: copy.label,
      stage: copy.stage,
      detail: copy.detail,
      action: copy.action,
    });
  }
  return views;
}

function Fact({
  label,
  value,
  color = C.fg3,
  tooltip,
  onOpen,
  onClose,
}: {
  label: string;
  value: string;
  color?: string;
  tooltip: string[];
  onOpen: (payload: HoverPayload) => void;
  onClose: () => void;
}) {
  const open = (event: MouseEvent<HTMLDivElement>) => {
    onOpen({
      x: event.clientX + 14,
      y: event.clientY + 14,
      title: label,
      lines: tooltip,
    });
  };

  return (
    <div
      className="flex cursor-help flex-col gap-0.5"
      onMouseEnter={open}
      onMouseMove={open}
      onMouseLeave={onClose}
      onFocus={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        onOpen({ x: rect.left, y: rect.bottom + 4, title: label, lines: tooltip });
      }}
      onBlur={onClose}
      tabIndex={0}
    >
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
      <code className="num min-w-0 truncate rounded px-1.5 py-px text-[10px]" style={{ color: C.fg2, background: C.subtleStrong }} title={path}>
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
        {copied ? <Check className="size-3" style={{ color: C.green }} /> : <Copy className="size-3" style={{ color: C.fg0 }} />}
      </button>
    </div>
  );
}

export function WireframeRunHeader({
  run,
  children,
  onAnnotateRun,
}: {
  run: RunRecord;
  children?: ReactNode;
  onAnnotateRun: () => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [factTooltip, setFactTooltip] = useState<HoverPayload | null>(null);
  const tier = scoreTier(run.unscored ? null : run.composite_score);
  const unscoredReasons = unscoredReasonViews(run.unscored_reasons);

  return (
    <div
      className="flex flex-col gap-2.5 rounded-lg p-3"
      style={{ background: C.surface, border: `1px solid ${C.border}`, borderLeft: `3px solid ${tier.color}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium" style={{ color: C.fg5 }}>
          {run.scenario}@{run.revision} · {runLabel(run.id)}
        </span>
        {run.synthetic && (
          <span
            className="rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide"
            style={{ color: '#f59e0b', borderColor: '#f59e0b66', background: 'rgba(245, 158, 11, 0.16)' }}
          >
            Synth
          </span>
        )}
        {!run.valid && (
          <span className="rounded px-1.5 py-px text-[9px] font-bold" style={{ color: C.red, background: 'rgba(235,20,20,0.1)' }}>
            invalid
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            className="inline-flex size-6 items-center justify-center rounded border transition hover:bg-white/10"
            style={{ color: C.accent, borderColor: C.selectedBorder }}
            title="Annotate this run"
            aria-label="Annotate this run"
            onClick={onAnnotateRun}
          >
            <MessageSquarePlus className="size-3.5" />
          </button>
          <Link
            to={`/#family-${encodeURIComponent(run.scenario)}`}
            className="inline-flex size-6 items-center justify-center rounded border transition hover:bg-white/10"
            style={{ color: C.accent, borderColor: C.selectedBorder }}
            title={`Compare agent specs for ${run.scenario}`}
            aria-label="Compare agent specs"
          >
            <ArrowUpRight className="size-3.5" />
          </Link>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-xs leading-relaxed" style={{ color: C.fg3 }}>
          {runSummary(run)}
        </span>
        <span className="num text-[10px]" style={{ color: C.cyan }} title={run.agent_spec}>
          {run.agent_spec}
        </span>
      </div>

      {children}

      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Fact
          label="score"
          value={fmtScore(run.composite_score)}
          color={tier.color}
          tooltip={[
            ...FACT_HELPERS.score,
            `composite: ${fmtScore(run.composite_score)}`,
            `quality: ${fmtScore(run.quality_score)}`,
            `diagnostic: ${fmtScore(run.diagnostic_score)}`,
          ]}
          onOpen={setFactTooltip}
          onClose={() => setFactTooltip(null)}
        />
        {run.total_input_tokens === 0 && run.total_output_tokens === 0 ? (
          <Fact
            label="tokens"
            value="not recorded"
            color={C.fg0}
            tooltip={[...FACT_HELPERS.tokens, 'No token usage captured for this run.']}
            onOpen={setFactTooltip}
            onClose={() => setFactTooltip(null)}
          />
        ) : (
          <Fact
            label="tokens"
            value={`${fmtTokens(run.total_input_tokens)} in · ${fmtTokens(run.total_output_tokens)} out`}
            tooltip={[
              ...FACT_HELPERS.tokens,
              `input: ${run.total_input_tokens.toLocaleString()}`,
              `output: ${run.total_output_tokens.toLocaleString()}`,
            ]}
            onOpen={setFactTooltip}
            onClose={() => setFactTooltip(null)}
          />
        )}
        <Fact label="run time" value={fmtDuration(run.duration_ms)} tooltip={[...FACT_HELPERS['run time'], `duration: ${fmtDuration(run.duration_ms)}`]} onOpen={setFactTooltip} onClose={() => setFactTooltip(null)} />
        <Fact label="steps" value={String(run.span_count)} tooltip={[...FACT_HELPERS.steps, `spans: ${run.span_count}`]} onOpen={setFactTooltip} onClose={() => setFactTooltip(null)} />
        <Fact label="diagnostic" value={fmtScore(run.diagnostic_score)} tooltip={[...FACT_HELPERS.diagnostic, `diagnostic: ${fmtScore(run.diagnostic_score)}`]} onOpen={setFactTooltip} onClose={() => setFactTooltip(null)} />
        <Fact label="status" value={run.status} color={run.status === 'OK' ? C.green : run.status === 'ERROR' ? C.red : C.fg2} tooltip={[...FACT_HELPERS.status, `status: ${run.status}`]} onOpen={setFactTooltip} onClose={() => setFactTooltip(null)} />
        <Fact label="valid" value={run.valid ? 'yes' : 'no'} color={run.valid ? C.green : C.red} tooltip={[...FACT_HELPERS.valid, `valid: ${run.valid ? 'yes' : 'no'}`]} onOpen={setFactTooltip} onClose={() => setFactTooltip(null)} />
      </div>

      {factTooltip ? (
        <div
          className="pointer-events-none fixed z-50 max-w-64 rounded-md border px-2 py-1.5 text-[10px] shadow-2xl"
          style={{
            left: factTooltip.x,
            top: factTooltip.y,
            color: C.fg3,
            background: C.tooltipBg,
            borderColor: C.tooltipBorder,
          }}
        >
          <div className="mb-1 text-[11px] font-medium capitalize" style={{ color: C.fg5 }}>
            {factTooltip.title}
          </div>
          {factTooltip.lines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      ) : null}

      {unscoredReasons.length > 0 && (
        <div className="flex flex-col gap-2 rounded-md p-2" style={{ background: `${C.orange}0d`, border: `1px solid ${C.orange}30` }}>
          <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: C.orange }}>
            Why this run is unscored
          </span>
          {unscoredReasons.map((reason) => (
            <div key={reason.id} className="flex flex-col gap-1 rounded border px-2 py-1.5" style={{ borderColor: `${C.orange}24`, background: C.subtle }}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[11px] font-medium" style={{ color: C.fg5 }}>
                  {reason.label}
                </span>
                <span className="num rounded px-1.5 py-px text-[9px]" style={{ color: C.orange, background: `${C.orange}14` }}>
                  {reason.stage}
                </span>
              </div>
              <span className="text-[11px]" style={{ color: C.fg3 }}>
                {reason.detail}
              </span>
              {reason.action ? (
                <span className="text-[10px]" style={{ color: C.fg1 }}>
                  {reason.action}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-1">
        <button className="flex items-center gap-1.5 text-left text-[10px]" style={{ color: C.fg0 }} onClick={() => setDetailsOpen((open) => !open)}>
          {detailsOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
          Run artifacts
        </button>
        {detailsOpen && (
          <div className="flex flex-col gap-1.5 rounded-md p-2" style={{ background: C.subtle }}>
            <ArtifactPath label="run json" path={run.artifact_paths.run_json} />
            {run.artifact_paths.findings_json && <ArtifactPath label="findings json" path={run.artifact_paths.findings_json} />}
          </div>
        )}
      </div>
    </div>
  );
}
