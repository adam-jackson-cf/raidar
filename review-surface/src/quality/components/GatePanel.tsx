// Verification effort of a gated trial (verification-effort@1 + the step's gate/1 record):
// per checkpoint, how the gate ended, the runs it took, the failed blocking checks and the
// fix sessions' cost and time. A step expands to its gate rounds: each run's verdict,
// attribution, duration and the names of the blocking checks that failed. A trial without a
// gate/1 record has no gate view, so nothing renders: never a row of zeros.
import { Fragment, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { C } from '@/presentation/colors';
import { StatusBadge } from '@/quality/components/QualityStrip';
import type { Cell, GateRound, GateStep, GateView } from '@/quality/types';

const pct = (value: number) => `${Math.round(value * 100)}%`;
const usd = (value: number) => `$${value.toFixed(3)}`;
const secs = (value: number) => `${value.toFixed(0)} s`;
const count = (value: number) => value.toFixed(0);
const mean = (value: number) => value.toFixed(2);

function CellValue({ cell, format }: { cell: Cell | undefined; format: (value: number) => string }) {
  if (!cell) return <span style={{ color: C.fg0 }}>—</span>;
  if (cell.status !== 'ok' || cell.value == null) return <StatusBadge cell={cell} />;
  return <span className="num">{format(cell.value)}</span>;
}

const TOTALS: Array<[string, string, (value: number) => string]> = [
  ['first_pass_clean_rate', 'First-pass clean', pct],
  ['clean_at_step_end_rate', 'Clean at step end', pct],
  ['unresolved_rate', 'Unresolved', pct],
  ['gate_error_rate', 'Gate errors', pct],
  ['mean_rounds_to_clean', 'Rounds to clean (mean)', mean],
  ['total_fix_rounds', 'Fix rounds', count],
  ['total_blocking_checks', 'Failed blocking checks', count],
  ['total_fix_cost_usd', 'Fix cost', usd],
  ['total_fix_wall_s', 'Fix time', secs],
];

function ending(step: GateStep): { label: string; color: string } {
  const flag = (metric: string) => step.cells[metric]?.value === 1;
  const fixes = step.cells.fix_rounds?.value ?? 0;
  if (flag('gate_error')) return { label: 'gate error', color: C.red };
  if (flag('unresolved')) return { label: 'unresolved', color: C.orange };
  if (flag('gate_clean_at_step_end')) {
    return fixes === 0
      ? { label: 'clean first pass', color: C.green }
      : { label: `clean after ${fixes} fix${fixes === 1 ? '' : 'es'}`, color: C.fg4 };
  }
  if (step.rounds.length === 0) return { label: 'no gate record', color: C.fg1 };
  return { label: 'not measured', color: C.fg1 };
}

function FailedChecks({ round }: { round: GateRound }) {
  if (round.failed_checks.length === 0) return null;
  return (
    <span style={{ color: C.fg2 }} title="Blocking checks that did not pass">
      failed: {round.failed_checks.join(' · ')}
    </span>
  );
}

function Rounds({ step }: { step: GateStep }) {
  const { rounds, errors } = step;
  if (rounds.length === 0 && errors.length === 0) {
    return <p style={{ color: C.fg1 }}>No gate run in this step.</p>;
  }
  return (
    <div className="flex flex-col gap-0.5">
      <ol aria-label="Gate rounds" className="flex flex-col gap-0.5">
        {rounds.map((round) => (
          <li key={round.run} data-testid="gate-round" data-verdict={round.verdict} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium" style={{ color: round.verdict === 'passed' ? C.green : round.verdict === 'error' ? C.red : C.fg4 }}>
              #{round.run} {round.verdict}
            </span>
            <span style={{ color: C.fg1 }}>after {round.after.replace(/^stage_\d+_/, '')} session</span>
            <span style={{ color: C.fg1 }}>{round.attribution.replace(/_/g, ' ')}</span>
            {round.seconds != null && (
              <span className="num" style={{ color: C.fg1 }}>
                {round.seconds.toFixed(1)} s
              </span>
            )}
            <FailedChecks round={round} />
            {round.error && <span style={{ color: C.red }}>{round.error}</span>}
          </li>
        ))}
      </ol>
      {errors.map((error) => (
        <p key={error} data-testid="gate-step-error" style={{ color: C.red }}>
          {error}
        </p>
      ))}
    </div>
  );
}

function StepRows({ step }: { step: GateStep }) {
  const [open, setOpen] = useState(false);
  const end = ending(step);
  return (
    <Fragment>
      <tr data-testid="gate-step" style={{ borderTop: `1px solid ${C.rowBorder}` }}>
        <td className="py-1 pr-2">
          <button
            type="button"
            className="q-focus flex items-center gap-1"
            aria-expanded={open}
            aria-label={`${step.checkpoint} gate rounds`}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            <span style={{ color: C.fg3 }}>{step.checkpoint.replace('checkpoint_', 'cp')}</span>
          </button>
        </td>
        <td className="pr-2" data-testid="gate-ending" style={{ color: end.color }}>
          {end.label}
        </td>
        <td className="pr-2 text-right">
          <CellValue cell={step.cells.gate_runs} format={count} />
        </td>
        <td className="pr-2 text-right">
          <CellValue cell={step.cells.first_run_blocking} format={count} />
          <span style={{ color: C.fg0 }}> / </span>
          <CellValue cell={step.cells.blocking_checks} format={count} />
        </td>
        <td className="pl-2 text-right">
          <CellValue cell={step.cells.fix_cost_usd} format={usd} />
        </td>
        <td className="pl-2 text-right">
          <CellValue cell={step.cells.fix_wall_s} format={secs} />
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="pb-1.5 pl-5">
            <Rounds step={step} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function GatePanel({ gate }: { gate: GateView | null | undefined }) {
  if (!gate) return null;
  return (
    <section aria-label="Verification effort" data-testid="gate-panel" className="flex flex-col gap-1.5 text-[10px]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[11px] font-medium" style={{ color: C.fg3 }}>
          Verification effort
        </span>
        <span style={{ color: C.fg0 }}>
          {gate.scorer ?? 'verification-effort@1'} · rounds, first-pass clean and failed blocking checks
        </span>
      </div>
      <dl data-testid="gate-totals" className="flex flex-wrap gap-x-4 gap-y-1">
        {TOTALS.map(([metric, label, format]) => (
          <div key={metric} className="flex items-baseline gap-1">
            <dt style={{ color: C.fg1 }}>{label}</dt>
            <dd style={{ color: C.fg4 }}>
              <CellValue cell={gate.totals[metric]} format={format} />
            </dd>
          </div>
        ))}
      </dl>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse" aria-label="Verification effort per checkpoint">
          <thead>
            <tr style={{ color: C.fg1 }}>
              <th className="pr-2 text-left font-normal">Checkpoint</th>
              <th className="pr-2 text-left font-normal">Ended</th>
              <th className="pr-2 text-right font-normal">Runs</th>
              <th className="pr-2 text-right font-normal" title="Failed blocking checks of the first run / over every run">
                Failed blocking checks 1st / all
              </th>
              <th className="pl-2 text-right font-normal">Fix cost</th>
              <th className="pl-2 text-right font-normal">Fix time</th>
            </tr>
          </thead>
          <tbody>
            {gate.steps.map((step) => (
              <StepRows key={step.checkpoint} step={step} />
            ))}
          </tbody>
        </table>
      </div>
      {gate.notes.map((note) => (
        <p key={note} data-testid="gate-note" style={{ color: C.orange }}>
          {note}
        </p>
      ))}
    </section>
  );
}
