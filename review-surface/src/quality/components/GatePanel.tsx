// Commit-gate effort of a gated trial (gate-effort + the step's guardrail-gate record):
// per checkpoint, how the gate ended, the rounds it took, first-pass clean, the blocking
// findings by rule family (a single-hue magnitude table, numbers always printed) and the
// fix sessions' cost and time. A step expands to its gate rounds. An ungated trial has no
// gate view, so nothing renders: never a row of zeros. A shadow gate (guardrail-gate@2
// mode shadow, gate-effort) was recorded at each step's end and never enforced: it is
// labelled as such, its families are the first run's and its fix outcomes read n/a.
import { Fragment, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { C } from '@/presentation/colors';
import { StatusBadge } from '@/quality/components/QualityStrip';
import { GATE_FAMILIES, type Cell, type GateRound, type GateStep, type GateView } from '@/quality/types';

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
  ['total_fix_cost_usd', 'Fix cost', usd],
  ['total_fix_wall_s', 'Fix time', secs],
];

function ending(step: GateStep): { label: string; color: string } {
  const fixes = step.cells.fix_rounds?.value ?? 0;
  switch (step.termination) {
    case 'shadow_recorded':
      return step.cells.first_pass_clean?.value === 1
        ? { label: 'shadow: would pass', color: C.green }
        : { label: 'shadow: would refuse', color: C.fg4 };
    case 'gate_clean':
      return fixes === 0
        ? { label: 'clean first pass', color: C.green }
        : { label: `clean after ${fixes} fix${fixes === 1 ? '' : 'es'}`, color: C.fg4 };
    case 'rounds_exhausted':
      return { label: 'unresolved', color: C.orange };
    case 'gate_error':
      return { label: 'gate error', color: C.red };
    case null:
      return { label: 'no gate record', color: C.fg1 };
    default:
      return { label: `ended: ${step.termination.replace(/_/g, ' ')}`, color: C.orange };
  }
}

function FamilyCell({ cell, max }: { cell: Cell; max: number }) {
  if (cell.status !== 'ok' || cell.value == null) return <StatusBadge cell={cell} />;
  const share = max > 0 ? cell.value / max : 0;
  return (
    <span
      className="num inline-block min-w-[1.75rem] rounded px-1 text-center"
      style={{
        color: cell.value === 0 ? C.fg0 : C.fg4,
        background: cell.value === 0 ? 'transparent' : `color-mix(in srgb, ${C.accent} ${Math.round(12 + 48 * share)}%, transparent)`,
      }}
    >
      {count(cell.value)}
    </span>
  );
}

function RoundFamilies({ round }: { round: GateRound }) {
  if (round.verdict === 'passed') return null;
  if (!round.families) {
    return (
      <span style={{ color: C.fg1 }}>
        families unavailable ({round.listed} of {round.blocking ?? '?'} findings listed)
      </span>
    );
  }
  const families = round.families;
  const named = GATE_FAMILIES.filter((family) => families[family] > 0);
  return <span style={{ color: C.fg2 }}>{named.map((family) => `${family} ${families[family]}`).join(' · ')}</span>;
}

function Rounds({ rounds }: { rounds: GateRound[] }) {
  if (rounds.length === 0) {
    return <p style={{ color: C.fg1 }}>No gate run in this step.</p>;
  }
  return (
    <ol aria-label="Gate rounds" className="flex flex-col gap-0.5">
      {rounds.map((round) => (
        <li key={round.run} data-testid="gate-round" data-verdict={round.verdict} className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium" style={{ color: round.verdict === 'passed' ? C.green : round.verdict === 'error' ? C.red : C.fg4 }}>
            #{round.run} {round.verdict.replace('_', ' ')}
          </span>
          <span style={{ color: C.fg1 }}>after {round.after.replace(/^stage_\d+_/, '')} session</span>
          {round.blocking != null && round.verdict !== 'passed' && (
            <span className="num" style={{ color: C.fg3 }}>
              {round.blocking} blocking
            </span>
          )}
          <RoundFamilies round={round} />
          {round.log_path && (
            <code className="min-w-0 truncate text-[9px]" style={{ color: C.fg0 }} title={round.log_path}>
              {round.log_path.split('/').slice(-2).join('/')}
            </code>
          )}
        </li>
      ))}
    </ol>
  );
}

function stepFamilies(step: GateStep, shadow: boolean): GateStep['families'] {
  return shadow ? step.first_run_families : step.families;
}

function StepRows({ step, max, shadow }: { step: GateStep; max: number; shadow: boolean }) {
  const [open, setOpen] = useState(false);
  const end = ending(step);
  const families = stepFamilies(step, shadow);
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
          <CellValue cell={step.cells.blocking_findings} format={count} />
        </td>
        {GATE_FAMILIES.map((family) => (
          <td key={family} className="px-0.5 text-center" title={`${family}: ${families[family]?.status === 'ok' ? families[family].value : families[family]?.reason ?? ''}`}>
            <FamilyCell cell={families[family]} max={max} />
          </td>
        ))}
        <td className="pl-2 text-right">
          <CellValue cell={step.cells.fix_cost_usd} format={usd} />
        </td>
        <td className="pl-2 text-right">
          <CellValue cell={step.cells.fix_wall_s} format={secs} />
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6 + GATE_FAMILIES.length} className="pb-1.5 pl-5">
            <Rounds rounds={step.rounds} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function GatePanel({ gate }: { gate: GateView | null | undefined }) {
  if (!gate) return null;
  const shadow = gate.shadow === true;
  const values = gate.steps.flatMap((step) => {
    const families = stepFamilies(step, shadow);
    return GATE_FAMILIES.map((family) => (families[family]?.status === 'ok' ? families[family].value ?? 0 : 0));
  });
  const max = Math.max(0, ...values);
  return (
    <section aria-label="Commit gate" data-testid="gate-panel" className="flex flex-col gap-1.5 text-[10px]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[11px] font-medium" style={{ color: C.fg3 }}>
          Commit gate
        </span>
        <span style={{ color: C.fg0 }}>
          {gate.scorer ?? 'gate-effort@10'} · rounds, first-pass clean and blocking findings by rule family
        </span>
        {shadow && (
          <span data-testid="gate-shadow" style={{ color: C.orange }} title="Recorded at each step's end, never enforced: no refusal, no fix session; the families shown are the first run's">
            shadow gate · never enforced
          </span>
        )}
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
        <table className="w-full border-collapse" aria-label="Gate effort per checkpoint">
          <thead>
            <tr style={{ color: C.fg1 }}>
              <th className="pr-2 text-left font-normal">Checkpoint</th>
              <th className="pr-2 text-left font-normal">Ended</th>
              <th className="pr-2 text-right font-normal">Runs</th>
              <th className="pr-2 text-right font-normal" title="Blocking findings of the first run / over every run">
                Blocking 1st / all
              </th>
              {GATE_FAMILIES.map((family) => (
                <th key={family} className="px-0.5 text-center font-normal" title={shadow ? `Blocking ${family} findings of the shadow run` : `Blocking ${family} findings over every run`}>
                  {family}
                </th>
              ))}
              <th className="pl-2 text-right font-normal">Fix cost</th>
              <th className="pl-2 text-right font-normal">Fix time</th>
            </tr>
          </thead>
          <tbody>
            {gate.steps.map((step) => (
              <StepRows key={step.checkpoint} step={step} max={max} shadow={shadow} />
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
