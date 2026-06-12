import { Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtPercent } from '@/utils/helpers';
import { NeutralChip } from '@/components/review/chips';
import { scoreColor } from '@/components/review/DimensionCells';
import type {
  EvidenceSide,
  OutcomeProof,
  ReviewEvidence,
  VisualProof,
} from '@/utils/review-types';

function artifactUrl(repoRelativePath: string): string {
  return `/artifacts/${repoRelativePath.split('/').map(encodeURIComponent).join('/')}`;
}

function EvidenceImage({ path, label, caption }: { path: string | null; label: string; caption?: string }) {
  return (
    <figure className="flex min-w-0 flex-1 flex-col gap-1">
      <figcaption className="text-[9px] uppercase tracking-wider" style={{ color: C.fg1 }}>
        {label}
      </figcaption>
      {path ? (
        <a href={artifactUrl(path)} target="_blank" rel="noreferrer" title={`Open ${label} asset`}>
          <img
            src={artifactUrl(path)}
            alt={label}
            className="w-full rounded"
            style={{ border: `1px solid ${C.border}`, background: '#000' }}
          />
        </a>
      ) : (
        <div
          className="flex h-24 items-center justify-center rounded text-[10px]"
          style={{ border: `1px dashed ${C.border}`, color: C.orange }}
        >
          Missing
        </div>
      )}
      {caption && (
        <span className="num text-[9px]" style={{ color: C.fg0 }}>
          {caption}
        </span>
      )}
    </figure>
  );
}

function RegionCards({
  current,
  comparator,
  comparatorLabel,
}: {
  current: VisualProof;
  comparator: VisualProof | null;
  comparatorLabel: string | null;
}) {
  const comparatorRegions = new Map((comparator?.regions ?? []).map((region) => [region.name, region]));
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {current.regions.map((region) => {
        const other = comparatorRegions.get(region.name) ?? null;
        const delta =
          region.median_score != null && other?.median_score != null
            ? region.median_score - other.median_score
            : null;
        const failed = (region.pass_rate ?? 1) < 1;
        return (
          <div
            key={region.name}
            className="flex flex-col gap-1 rounded-lg p-2"
            style={{
              background: C.surface,
              border: `1px solid ${failed ? 'rgba(240,173,78,0.35)' : C.border}`,
            }}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[11px] font-medium" style={{ color: C.fg4 }}>
                {region.name}
              </span>
              <span className="num text-[11px]" style={{ color: region.median_score != null ? scoreColor(region.median_score) : C.fg0 }}>
                {region.median_score?.toFixed(2) ?? '—'}
                {region.threshold != null && (
                  <span style={{ color: C.fg0 }}> / {region.threshold.toFixed(2)} bar</span>
                )}
              </span>
            </div>
            <div className="flex items-center justify-between text-[10px]">
              <span style={{ color: failed ? C.orange : C.green }}>
                {fmtPercent(region.pass_rate)} of runs meet the bar
              </span>
              {delta != null && (
                <span className="num" style={{ color: delta >= 0 ? C.green : C.orange }} title={comparatorLabel ?? ''}>
                  {delta >= 0 ? '+' : ''}
                  {delta.toFixed(2)} vs {comparatorLabel?.split(' ')[0] ?? 'comparator'}
                </span>
              )}
            </div>
            {region.diff_path && (
              <a
                href={artifactUrl(region.diff_path)}
                target="_blank"
                rel="noreferrer"
                title={`Open ${region.name} diff evidence`}
              >
                <img
                  src={artifactUrl(region.diff_path)}
                  alt={`${region.name} diff`}
                  className="h-12 w-full rounded object-cover object-top"
                  style={{ border: `1px solid ${C.border}` }}
                />
              </a>
            )}
          </div>
        );
      })}
    </div>
  );
}

function VisualStrip({
  current,
  comparator,
  comparatorLabel,
}: {
  current: VisualProof;
  comparator: VisualProof | null;
  comparatorLabel: string | null;
}) {
  const delta =
    current.similarity_median != null && comparator?.similarity_median != null
      ? current.similarity_median - comparator.similarity_median
      : null;
  return (
    <div className="rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
          Visual evidence — reference vs delivered
        </span>
        <span className="num text-[10px]" style={{ color: C.fg1 }}>
          similarity median {current.similarity_median?.toFixed(3) ?? '—'} · {fmtPercent(current.threshold_pass_rate)} of runs met threshold
        </span>
        {current.capture_failures > 0 && (
          <span className="text-[10px]" style={{ color: C.red }}>
            {current.capture_failures} run(s) failed screenshot capture
          </span>
        )}
        {delta != null && (
          <span className="num text-[10px]" style={{ color: delta >= 0 ? C.green : C.orange }}>
            {delta >= 0 ? '+' : ''}
            {delta.toFixed(3)} similarity vs {comparatorLabel}
          </span>
        )}
      </div>
      <div className="mb-2 flex flex-col gap-2 sm:flex-row">
        <EvidenceImage path={current.reference_path} label="reference" />
        <EvidenceImage
          path={current.actual_path}
          label="current"
          caption={`anchor run ${current.anchor_run}`}
        />
        {comparator && (
          <EvidenceImage
            path={comparator.actual_path}
            label={comparatorLabel ?? 'comparator'}
            caption={`similarity ${comparator.similarity_median?.toFixed(3) ?? '—'}`}
          />
        )}
        <EvidenceImage path={current.diff_path} label="diff vs reference" />
      </div>
      <RegionCards current={current} comparator={comparator} comparatorLabel={comparatorLabel} />
    </div>
  );
}

function BlockTitle({ title, status }: { title: string; status?: string }) {
  return (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.fg1 }}>
        {title}
      </span>
      {status && status !== 'Present' && (
        <span className="text-[10px]" style={{ color: C.orange }}>
          {status}
        </span>
      )}
    </div>
  );
}

function AnchorLine({ side }: { side: EvidenceSide }) {
  if (!side.anchor) return null;
  return (
    <div className="mb-1 flex items-center gap-1 text-[10px]" style={{ color: C.fg0 }}>
      evidence anchor run
      <Link
        to={`/runs/${encodeURIComponent(side.anchor.run_id)}`}
        className="inline-flex items-center gap-0.5 transition hover:opacity-80"
        style={{ color: C.accent }}
      >
        {side.anchor.run_id} <ExternalLink className="size-2.5" />
      </Link>
      {side.anchor.atypical && (
        <span style={{ color: C.orange }}>atypical — no valid scored run was available</span>
      )}
    </div>
  );
}

function OutcomeBlock({ outcome }: { outcome: OutcomeProof | null }) {
  if (!outcome) {
    return (
      <span className="text-[11px]" style={{ color: C.fg0 }}>
        No scored outcome evidence.
      </span>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <table className="w-full border-collapse">
        <thead>
          <tr style={{ borderBottom: `1px solid ${C.border}` }}>
            {['Check', 'Type', 'Pass rate', 'Median'].map((h) => (
              <th key={h} className="px-1.5 py-1 text-left text-[9px] font-medium uppercase tracking-wider" style={{ color: C.fg0 }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {outcome.checks.map((check) => (
            <tr key={check.name} style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
              <td className="px-1.5 py-1 text-[11px]" style={{ color: C.fg3 }}>
                {check.name}
                {check.missing_patterns.length > 0 && (
                  <span className="ml-1.5 text-[9px]" style={{ color: C.orange }} title={`Missing: ${check.missing_patterns.join(', ')}`}>
                    missing {check.missing_patterns.join(', ')}
                  </span>
                )}
                {check.evidence && (
                  <div className="text-[9px]" style={{ color: C.fg0 }}>
                    {check.evidence}
                  </div>
                )}
              </td>
              <td className="px-1.5 py-1 text-[9px] uppercase" style={{ color: check.kind === 'judge' ? C.purple : C.fg1 }}>
                {check.kind}
              </td>
              <td className="num px-1.5 py-1 text-[11px]" style={{ color: check.pass_rate != null && check.pass_rate < 1 ? C.orange : C.fg2 }}>
                {fmtPercent(check.pass_rate)}
              </td>
              <td className="num px-1.5 py-1 text-[11px]" style={{ color: check.median_score != null ? scoreColor(check.median_score) : C.fg0 }}>
                {check.median_score?.toFixed(2) ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {outcome.requirements && (
        <div className="text-[10px]" style={{ color: C.fg1 }}>
          Requirements: {outcome.requirements.total} authored · presence {fmtPercent(outcome.requirements.presence_ratio)} · test mapping {fmtPercent(outcome.requirements.mapping_ratio)}
          {outcome.requirements.mapping_ratio === 0 && (
            <span style={{ color: C.orange }}> — adherence is unproven without requirement-to-test mapping</span>
          )}
          {outcome.requirements.missing_ids.length > 0 && (
            <span style={{ color: C.red }}> · missing: {outcome.requirements.missing_ids.join(', ')}</span>
          )}
        </div>
      )}
    </div>
  );
}

function ImplementationBlock({ side }: { side: EvidenceSide }) {
  if (!side.implementation) {
    return (
      <span className="text-[11px]" style={{ color: C.fg0 }}>
        No retained file-change evidence.
      </span>
    );
  }
  return (
    <div className="flex flex-col gap-0.5">
      {side.implementation.files.map((file) => (
        <div key={file.path} className="flex items-center justify-between gap-2">
          <span className="num truncate text-[11px]" style={{ color: C.fg3 }} title={file.path}>
            {file.path}
          </span>
          <span className="num shrink-0 text-[10px]" style={{ color: C.fg0 }}>
            {file.runs_touched}/{side.implementation!.run_count} runs
          </span>
        </div>
      ))}
    </div>
  );
}

function VerificationBlock({ side }: { side: EvidenceSide }) {
  const proof = side.verification;
  if (!proof) {
    return (
      <span className="text-[11px]" style={{ color: C.fg0 }}>
        No verification evidence.
      </span>
    );
  }
  return (
    <div className="flex flex-col gap-1 text-[11px]">
      <span style={{ color: proof.first_pass_rate != null && proof.first_pass_rate < 1 ? C.orange : C.fg2 }}>
        First-pass verification success: {fmtPercent(proof.first_pass_rate)}
        {proof.gates_per_run > 0 && (
          <span style={{ color: C.fg0 }}> · {proof.gates_per_run} authored gates per run</span>
        )}
      </span>
      {proof.gate_failures.map((gate) => (
        <div key={gate.name} style={{ color: C.orange }}>
          gate “{gate.name}” failed ×{gate.failures}
          {gate.last_detail && (
            <span style={{ color: C.fg1 }}> — {gate.last_detail}</span>
          )}
        </div>
      ))}
      {proof.required_command_misses.map((miss) => (
        <div key={miss} style={{ color: C.red }}>
          {miss}
        </div>
      ))}
      {proof.gate_failures.length === 0 && proof.required_command_misses.length === 0 && (
        <span style={{ color: C.green }}>All authored verification ran cleanly.</span>
      )}
    </div>
  );
}

function SideBySide({
  title,
  status,
  current,
  comparator,
  comparatorLabel,
  render,
}: {
  title: string;
  status?: string;
  current: EvidenceSide;
  comparator: EvidenceSide | null;
  comparatorLabel: string | null;
  render: (side: EvidenceSide) => JSX.Element;
}) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <BlockTitle title={title} status={status} />
      <div className={`grid gap-3 ${comparator ? 'lg:grid-cols-2' : ''}`}>
        <div>
          {comparator && (
            <div className="mb-1 text-[9px] uppercase tracking-wider" style={{ color: C.cyan }}>
              current
            </div>
          )}
          {render(current)}
        </div>
        {comparator && (
          <div style={{ borderLeft: `1px solid ${C.border}` }} className="lg:pl-3">
            <div className="mb-1 text-[9px] uppercase tracking-wider" style={{ color: C.accent }}>
              {comparatorLabel}
            </div>
            {render(comparator)}
          </div>
        )}
      </div>
    </div>
  );
}

export function EvidenceStrip({
  evidence,
  comparator,
  comparatorLabel,
  subtype,
}: {
  evidence: ReviewEvidence;
  comparator: EvidenceSide | null;
  comparatorLabel: string | null;
  subtype: string;
}) {
  const statusOf = (block: string) => evidence.availability.find((a) => a.block === block)?.status;
  return (
    <div id="evidence" className="flex scroll-mt-4 flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Evidence
        </span>
        <NeutralChip label={subtype} title="Scenario-family fidelity subtype for this evidence model" />
        {evidence.availability.map((a) => (
          <span key={a.block} className="text-[10px]" style={{ color: a.status === 'Present' ? C.fg0 : C.orange }}>
            {a.block}: {a.status.toLowerCase()}
          </span>
        ))}
        {!comparator && (
          <span className="text-[10px]" style={{ color: C.orange }}>
            no benchmark evidence to compare — conclusions stay one-sided
          </span>
        )}
      </div>
      <AnchorLine side={evidence.current} />
      {evidence.current.visual && (
        <VisualStrip
          current={evidence.current.visual}
          comparator={comparator?.visual ?? null}
          comparatorLabel={comparatorLabel}
        />
      )}
      <SideBySide
        title="Outcome proof — authored checks and requirements"
        status={statusOf('Outcome proof')}
        current={evidence.current}
        comparator={comparator}
        comparatorLabel={comparatorLabel}
        render={(side) => <OutcomeBlock outcome={side.outcome} />}
      />
      <div className="grid gap-2 lg:grid-cols-2">
        <SideBySide
          title="Implementation proof — changed files"
          status={statusOf('Implementation proof')}
          current={evidence.current}
          comparator={comparator}
          comparatorLabel={comparatorLabel}
          render={(side) => <ImplementationBlock side={side} />}
        />
        <SideBySide
          title="Verification proof — gates and required commands"
          status={statusOf('Verification proof')}
          current={evidence.current}
          comparator={comparator}
          comparatorLabel={comparatorLabel}
          render={(side) => <VerificationBlock side={side} />}
        />
      </div>
    </div>
  );
}
