import { C } from '@/utils/colors';
import type {
  AbsoluteStatus,
  ConfidenceBand,
  DeltaSummary,
  RepresentativeInfo,
} from '@/utils/review-types';

function Chip({
  label,
  fg,
  bg,
  border,
  title,
}: {
  label: string;
  fg: string;
  bg: string;
  border: string;
  title?: string;
}) {
  return (
    <span
      className="inline-flex h-[18px] shrink-0 items-center whitespace-nowrap rounded-full px-2 text-[10px] font-medium leading-[18px]"
      style={{ color: fg, background: bg, border: `1px solid ${border}` }}
      title={title}
    >
      {label}
    </span>
  );
}

const STATUS_STYLES: Record<AbsoluteStatus, { fg: string; bg: string; border: string }> = {
  'Meets Scenario Bar': { fg: C.green, bg: 'rgba(96,227,109,0.08)', border: 'rgba(96,227,109,0.3)' },
  'Below Scenario Bar': { fg: C.orange, bg: 'rgba(240,173,78,0.08)', border: 'rgba(240,173,78,0.3)' },
  Unavailable: { fg: C.fg1, bg: 'rgba(255,255,255,0.04)', border: C.border },
};

export function StatusChip({ status, title }: { status: AbsoluteStatus; title?: string }) {
  const style = STATUS_STYLES[status];
  return <Chip label={status} title={title} {...style} />;
}

const CONFIDENCE_STYLES: Record<ConfidenceBand, { fg: string; border: string }> = {
  High: { fg: C.green, border: 'rgba(96,227,109,0.3)' },
  Medium: { fg: C.cyan, border: 'rgba(79,202,227,0.3)' },
  Low: { fg: C.orange, border: 'rgba(240,173,78,0.3)' },
  'Very Low': { fg: C.red, border: 'rgba(235,20,20,0.3)' },
};

export function ConfidenceChip({ band, title }: { band: ConfidenceBand; title?: string }) {
  const style = CONFIDENCE_STYLES[band];
  return (
    <Chip
      label={`${band} confidence`}
      title={title}
      fg={style.fg}
      bg="rgba(255,255,255,0.03)"
      border={style.border}
    />
  );
}

const DELTA_STYLES: Record<DeltaSummary, { fg: string; border: string; label: string }> = {
  Ahead: { fg: C.green, border: 'rgba(96,227,109,0.3)', label: 'Ahead of benchmark' },
  Parity: { fg: C.fg2, border: C.borderLight, label: 'Parity with benchmark' },
  Behind: { fg: C.orange, border: 'rgba(240,173,78,0.3)', label: 'Behind benchmark' },
  Mixed: { fg: C.cyan, border: 'rgba(79,202,227,0.3)', label: 'Mixed vs benchmark' },
  Inconclusive: { fg: C.fg1, border: C.border, label: 'Inconclusive vs benchmark' },
  Unavailable: { fg: C.fg0, border: C.border, label: 'Benchmark unavailable' },
  Benchmark: { fg: C.accent, border: C.selectedBorder, label: 'Pinned benchmark' },
};

export function DeltaChip({ summary, title }: { summary: DeltaSummary; title?: string }) {
  const style = DELTA_STYLES[summary];
  return (
    <Chip label={style.label} title={title} fg={style.fg} bg="rgba(255,255,255,0.03)" border={style.border} />
  );
}

export function RepresentativeBadge({ representative }: { representative: RepresentativeInfo }) {
  const { scored_count, total_count, below_minimum, reason } = representative;
  return (
    <Chip
      label={below_minimum ? `${scored_count} scored · weak sample` : `×${scored_count} scored`}
      title={`${reason} (${scored_count}/${total_count} runs scored)`}
      fg={below_minimum ? C.orange : C.fg1}
      bg="rgba(255,255,255,0.03)"
      border={below_minimum ? 'rgba(240,173,78,0.3)' : C.border}
    />
  );
}

export function NeutralChip({ label, title }: { label: string; title?: string }) {
  return <Chip label={label} title={title} fg={C.fg1} bg="rgba(255,255,255,0.04)" border={C.border} />;
}
