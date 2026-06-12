import { Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import { C } from '@/utils/colors';
import { fmtTokens } from '@/utils/helpers';
import { scoreColor } from '@/components/review/DimensionCells';
import {
  DIMENSION_KEYS,
  DIMENSION_LABELS,
  type ConfidenceInfo,
  type RunConsistencyRow,
} from '@/utils/review-types';

const TH = 'px-2 py-1 text-left text-[9px] font-medium uppercase tracking-wider';

function DimDot({ value }: { value: number | null }) {
  if (value == null) {
    return <span style={{ color: C.fg0 }}>—</span>;
  }
  return (
    <span className="num inline-flex items-center gap-1 text-[10px]" style={{ color: scoreColor(value) }}>
      <span className="inline-block size-1.5 rounded-full" style={{ background: scoreColor(value) }} />
      {value.toFixed(2)}
    </span>
  );
}

export function RunConsistency({
  rows,
  confidence,
}: {
  rows: RunConsistencyRow[];
  confidence: ConfidenceInfo;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-xs font-medium" style={{ color: C.fg3 }}>
          Run consistency
        </span>
        <span className="text-[10px]" style={{ color: C.fg0 }}>
          {confidence.components.map((component) => `${component.name} ${component.value ?? '—'}`).join(' · ')}
          {confidence.spread != null && ` · cross-run spread ${confidence.spread}`}
        </span>
      </div>
      <div className="sb overflow-x-auto rounded-lg" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
        <table className="w-full border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th className={TH} style={{ color: C.fg0 }}>Run</th>
              <th className={TH} style={{ color: C.fg0 }}>State</th>
              {DIMENSION_KEYS.map((key) => (
                <th key={key} className={TH} style={{ color: C.fg0 }}>
                  {DIMENSION_LABELS[key]}
                </th>
              ))}
              <th className={TH} style={{ color: C.fg0 }}>Duration</th>
              <th className={TH} style={{ color: C.fg0 }}>Tokens</th>
              <th className={TH} style={{ color: C.fg0 }}>Issues</th>
              <th className={TH} style={{ color: C.fg0 }}>Outlier</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.run_id}
                style={{
                  borderBottom: '1px solid rgba(255,255,255,0.03)',
                  background: row.outlier ? 'rgba(240,173,78,0.04)' : 'transparent',
                }}
              >
                <td className="px-2 py-1">
                  <Link
                    to={`/runs/${encodeURIComponent(row.run_id)}`}
                    className="num inline-flex items-center gap-1 text-[11px] transition hover:opacity-80"
                    style={{ color: C.accent }}
                  >
                    {row.run_id} <ExternalLink className="size-2.5" />
                  </Link>
                </td>
                <td className="px-2 py-1 text-[10px]">
                  <span style={{ color: row.scored ? C.green : C.red }}>{row.scored ? 'scored' : 'unscored'}</span>
                  <span style={{ color: C.fg0 }}> · </span>
                  <span style={{ color: row.valid ? C.fg1 : C.red }}>{row.valid ? 'valid' : 'invalid'}</span>
                </td>
                {DIMENSION_KEYS.map((key) => (
                  <td key={key} className="px-2 py-1">
                    <DimDot value={row.dimensions[key]} />
                  </td>
                ))}
                <td className="num px-2 py-1 text-[10px]" style={{ color: C.fg1 }}>
                  {row.duration_sec != null ? `${row.duration_sec}s` : '—'}
                </td>
                <td className="num px-2 py-1 text-[10px]" style={{ color: C.fg1 }}>
                  {fmtTokens(row.uncached_input_tokens)}
                </td>
                <td className="num px-2 py-1 text-[10px]" style={{ color: row.issues > 0 ? C.orange : C.fg0 }}>
                  {row.issues}
                </td>
                <td className="px-2 py-1 text-[10px]" style={{ color: row.outlier ? C.orange : C.fg0 }}>
                  {row.outlier ? row.outlier_reasons.join('; ') : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
