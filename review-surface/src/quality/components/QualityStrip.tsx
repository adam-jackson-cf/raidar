// Per-checkpoint C·V·E·H·R·G·I·S strip. Each cell is a progression ring for an
// ok value, or a grey diamond plus a status badge carrying the scorer's reason
// (never a zero). V/E/H/R/G cells open their file:line drill-down.
import { C } from '@/presentation/colors';
import {
  COMPONENT_INFO,
  STATUS_BADGE,
  cellColor,
  cellTitle,
  fmtComponent,
  unitValue,
} from '@/quality/format';
import {
  COMPONENTS,
  DRILLDOWN_COMPONENTS,
  type Cell,
  type CheckpointRow,
  type Component,
  type DrilldownComponent,
} from '@/quality/types';

export interface Selection {
  component: DrilldownComponent;
  checkpoint: string | null;
}

function isDrilldown(component: Component): component is DrilldownComponent {
  return (DRILLDOWN_COMPONENTS as readonly string[]).includes(component);
}

export function Ring({ component, cell }: { component: Component; cell: Cell }) {
  const fill = cell.value == null ? 0 : Math.max(0, Math.min(1, unitValue(component, cell.value)));
  return (
    <span
      className="inline-flex size-4 shrink-0 rounded-full"
      style={{ background: `conic-gradient(${cellColor(component, cell)} ${fill * 360}deg, ${C.ringTrack} 0deg)` }}
      aria-hidden
    >
      <span className="m-[3px] flex-1 rounded-full" style={{ background: C.surface }} />
    </span>
  );
}

export function StatusBadge({ cell }: { cell: Cell }) {
  if (cell.status === 'ok') return null;
  return (
    <span
      data-testid="eligibility-badge"
      data-status={cell.status}
      title={cell.reason ?? undefined}
      className="inline-flex items-center gap-1 rounded px-1 py-px text-[9px] font-semibold"
      style={{
        color: cell.status === 'not_applicable' ? C.fg1 : C.orange,
        border: `1px solid ${cell.status === 'not_applicable' ? C.borderLight : `${C.orange}66`}`,
      }}
    >
      <span className="inline-block size-1.5 rotate-45" style={{ background: 'currentColor' }} />
      {STATUS_BADGE[cell.status]}
    </span>
  );
}

function CellButton({
  component,
  cell,
  selected,
  onSelect,
}: {
  component: Component;
  cell: Cell;
  selected: boolean;
  onSelect?: () => void;
}) {
  const content = (
    <span className="inline-flex items-center gap-1.5">
      {cell.status === 'ok' ? (
        <>
          <Ring component={component} cell={cell} />
          <span className="num text-[10px]" style={{ color: C.fg2 }}>
            {fmtComponent(component, cell.value)}
          </span>
        </>
      ) : (
        <StatusBadge cell={cell} />
      )}
    </span>
  );
  const label = cellTitle(component, cell);
  if (!onSelect) {
    return (
      <span title={label} aria-label={label}>
        {content}
      </span>
    );
  }
  return (
    <button
      type="button"
      className="q-focus rounded px-1 py-0.5 transition hover:bg-white/5"
      style={{ background: selected ? C.selected : undefined, outline: selected ? `1px solid ${C.selectedBorder}` : undefined }}
      title={label}
      aria-label={`${label}. Show evidence`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      {content}
    </button>
  );
}

export function QualityStrip({
  rows,
  problem,
  policyRef,
  selection,
  onSelect,
}: {
  rows: CheckpointRow[];
  problem: Record<Component, Cell>;
  policyRef: string;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left" data-testid="quality-strip">
        <thead>
          <tr style={{ borderBottom: `1px solid ${C.border}` }}>
            <th className="px-2 py-1.5 text-[10px] font-medium" style={{ color: C.fg1 }}>
              Checkpoint
            </th>
            {COMPONENTS.map((component) => (
              <th
                key={component}
                scope="col"
                className="px-2 py-1.5 text-center text-[11px] font-semibold"
                style={{ color: C.fg3 }}
                title={`${COMPONENT_INFO[component].name}: ${COMPONENT_INFO[component].definition}`}
              >
                {isDrilldown(component) ? (
                  <button
                    type="button"
                    className="q-focus rounded px-1"
                    aria-label={`${COMPONENT_INFO[component].name} evidence, all checkpoints`}
                    onClick={() => onSelect({ component, checkpoint: null })}
                  >
                    {component}
                  </button>
                ) : (
                  component
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.checkpoint} data-testid="strip-row" style={{ borderBottom: `1px solid ${C.rowBorder}` }}>
              <th scope="row" className="whitespace-nowrap px-2 py-1 text-[11px] font-normal" style={{ color: C.fg3 }}>
                {row.checkpoint.replace('checkpoint_', 'cp ')}
                <span className="ml-1.5 text-[9px]" style={{ color: C.fg0 }}>
                  {row.state.replace(/_/g, ' ')}
                </span>
              </th>
              {COMPONENTS.map((component) => (
                <td key={component} className="px-1 py-1 text-center">
                  <CellButton
                    component={component}
                    cell={row.cells[component]}
                    selected={selection?.component === component && selection.checkpoint === row.checkpoint}
                    onSelect={isDrilldown(component) ? () => onSelect({ component, checkpoint: row.checkpoint }) : undefined}
                  />
                </td>
              ))}
            </tr>
          ))}
          <tr data-testid="strip-problem" style={{ borderTop: `1px solid ${C.borderLight}` }}>
            <th scope="row" className="whitespace-nowrap px-2 py-1.5 text-[11px] font-semibold" style={{ color: C.fg4 }}>
              Problem
              <span className="ml-1.5 text-[9px] font-normal" style={{ color: C.fg0 }}>
                {policyRef}
              </span>
            </th>
            {COMPONENTS.map((component) => (
              <td key={component} className="px-1 py-1.5 text-center">
                <CellButton component={component} cell={problem[component]} selected={false} />
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
