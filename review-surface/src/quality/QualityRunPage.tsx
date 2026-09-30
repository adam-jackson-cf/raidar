// Quality element, run detail: the per-checkpoint C·V·E·H·R·G·I·S strip with
// eligibility badges, V/E/H/R/G drill-downs to file:line evidence, and the
// run's Jev decisions for calibration labelling.
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { C } from '@/utils/colors';
import { qualityApi } from '@/quality/api';
import { EvidenceDrilldown } from '@/quality/components/EvidenceDrilldown';
import { JevLabelPanel } from '@/quality/components/JevLabelPanel';
import { QualityStrip, type Selection } from '@/quality/components/QualityStrip';
import { POLICY_SOURCE_TITLE } from '@/quality/format';
import '@/quality/quality.css';

export function QualityRunPage() {
  const { runId = '' } = useParams();
  const detail = useQuery({ queryKey: ['quality-run', runId], queryFn: () => qualityApi.run(runId) });
  const [selection, setSelection] = useState<Selection | null>(null);

  if (detail.isLoading) {
    return <p className="p-6 text-xs" style={{ color: C.fg1 }}>Loading quality run…</p>;
  }
  if (detail.isError || !detail.data) {
    return (
      <main className="p-6 text-xs" style={{ color: C.fg2 }}>
        <p>No quality data for {runId}.</p>
        <Link to="/quality" className="q-focus mt-2 inline-block" style={{ color: C.accent }}>
          Back to Quality
        </Link>
      </main>
    );
  }
  const data = detail.data;
  const run = data.run;
  return (
    <main className="min-h-0 flex-1 overflow-y-auto px-6 py-4" data-testid="quality-run-page">
      <Link to="/quality" className="q-focus mb-3 inline-flex items-center gap-1 text-[11px]" style={{ color: C.fg2 }}>
        <ArrowLeft className="size-3" /> Quality
      </Link>
      <header className="mb-3">
        <h1 className="text-sm font-semibold" style={{ color: C.fg5 }}>
          {run.trial} <span className="font-normal" style={{ color: C.fg2 }}>{run.arm}</span>
        </h1>
        <p className="text-[11px]" style={{ color: C.fg1 }}>
          {run.experiment} · {run.run} · block {run.block} · {run.problem} ·{' '}
          <span title={POLICY_SOURCE_TITLE[data.policy.source]}>
            {data.policy.ref}
          </span>
          {!run.valid && <span style={{ color: C.orange }}> · invalid trajectory (excluded from arm statistics)</span>}
        </p>
        {data.notes.map((note) => (
          <p key={note} className="text-[10px]" style={{ color: C.orange }}>
            {note}
          </p>
        ))}
      </header>
      <section aria-label="Checkpoint strip" className="rounded-md p-2" style={{ border: `1px solid ${C.border}`, background: C.surface }}>
        <QualityStrip
          rows={data.rows}
          problem={data.problem}
          policyRef={data.policy.ref}
          selection={selection}
          onSelect={setSelection}
        />
        {data.policy_reasons.length > 0 && (
          <ul className="mt-2 px-2 text-[10px]" style={{ color: C.fg2 }} data-testid="policy-reasons">
            {data.policy_reasons.map((reason) => (
              <li key={reason}>Headline S not eligible: {reason}</li>
            ))}
          </ul>
        )}
      </section>
      <div className="mt-4">
        {selection ? (
          <EvidenceDrilldown
            selection={selection}
            evidence={data.evidence}
            exclusions={data.exclusions}
            onClose={() => setSelection(null)}
          />
        ) : (
          <p className="text-[11px]" style={{ color: C.fg1 }}>
            Select a V, E, H, R or G cell to see its file:line evidence.
          </p>
        )}
      </div>
      <section className="mt-6" aria-label="Jev decisions">
        <h2 className="mb-1 text-xs font-semibold" style={{ color: C.fg4 }}>
          Jev decisions
        </h2>
        <JevLabelPanel runId={run.id} decisions={data.jev_decisions} />
      </section>
    </main>
  );
}
