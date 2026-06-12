// Adapted from Raindrop Workshop (MIT) — app/src/components/RunDetail.tsx (header)
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Copy, Check } from 'lucide-react';
import { Badge } from '@/components/Badge';
import { C } from '@/utils/colors';
import { fmtDuration, fmtScore, fmtTokens } from '@/utils/helpers';
import type { RunRecord } from '@/utils/types';

/** Review id is the experiment directory name under experiments/benchmarks/. */
function reviewIdFromRun(run: RunRecord): string | null {
  const match = run.artifact_paths.run_json.match(/experiments\/benchmarks\/([^/]+)\//);
  return match ? match[1] : null;
}

function StatusPill({ status }: { status: RunRecord['status'] }) {
  const color = status === 'OK' ? C.green : status === 'ERROR' ? C.red : C.fg1;
  return (
    <span
      className="num inline-flex items-center gap-1.5 rounded-full px-2 py-px text-[10px] font-bold"
      style={{ color, background: `${color}14`, border: `1px solid ${color}40` }}
    >
      <span className="size-1.5 rounded-full" style={{ background: color }} />
      {status}
    </span>
  );
}

function Metric({ label, value, color = C.fg3 }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
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
  const reviewId = reviewIdFromRun(run);
  return (
    <div
      className="flex flex-col gap-3 rounded-lg p-3"
      style={{ background: C.surface, border: `1px solid ${C.border}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium" style={{ color: C.fg5 }}>
          {run.name}
        </span>
        <StatusPill status={run.status} />
        <span
          className="num rounded px-1.5 py-px text-[10px]"
          style={{ color: C.cyan, background: `${C.cyan}12`, border: `1px solid ${C.cyan}30` }}
        >
          {run.agent_spec}
        </span>
        {run.synthetic && <Badge label="synthetic" />}
        {!run.valid && <Badge label="invalid" color={C.red} title="Run failed validity checks" />}
        <span className="num text-[10px]" style={{ color: C.fg0 }}>
          {run.id}
        </span>
        {reviewId && (
          <Link
            to={`/review/${encodeURIComponent(reviewId)}`}
            className="ml-auto inline-flex items-center gap-1 text-[10px] transition hover:opacity-80"
            style={{ color: C.accent }}
            title="Open the experiment review this run belongs to"
          >
            experiment review <ArrowUpRight className="size-3" />
          </Link>
        )}
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Metric label="composite" value={fmtScore(run.composite_score)} color={C.accent} />
        <Metric label="quality" value={fmtScore(run.quality_score)} />
        <Metric label="diagnostic" value={fmtScore(run.diagnostic_score)} />
        <Metric label="duration" value={fmtDuration(run.duration_ms)} />
        <Metric label="tokens in" value={fmtTokens(run.total_input_tokens)} />
        <Metric label="tokens out" value={fmtTokens(run.total_output_tokens)} />
        <Metric label="spans" value={String(run.span_count)} />
        <Metric label="valid" value={run.valid ? 'yes' : 'no'} color={run.valid ? C.green : C.red} />
      </div>

      {children}

      {run.unscored_reasons.length > 0 && (
        <div
          className="flex flex-col gap-1 rounded-md p-2"
          style={{ background: `${C.orange}0d`, border: `1px solid ${C.orange}30` }}
        >
          <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: C.orange }}>
            Unscored
          </span>
          {run.unscored_reasons.map((reason, i) => (
            <span key={i} className="text-[11px]" style={{ color: C.fg3 }}>
              {reason}
            </span>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-1">
        <ArtifactPath label="run json" path={run.artifact_paths.run_json} />
        {run.artifact_paths.findings_json && (
          <ArtifactPath label="findings json" path={run.artifact_paths.findings_json} />
        )}
      </div>
    </div>
  );
}
