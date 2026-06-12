import { Link } from 'react-router-dom';
import { ExternalLink, FlaskConical } from 'lucide-react';
import { C } from '@/utils/colors';
import { NeutralChip } from '@/components/review/chips';
import type { DiagnosisItem, Recommendation } from '@/utils/review-types';

function EvidenceLinks({ refs }: { refs: DiagnosisItem['evidence'] }) {
  if (!refs.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {refs.map((ref, index) =>
        ref.run_id ? (
          <Link
            key={index}
            to={`/runs/${encodeURIComponent(ref.run_id)}`}
            className="inline-flex items-center gap-0.5 text-[10px] transition hover:opacity-80"
            style={{ color: C.accent }}
          >
            {ref.label} <ExternalLink className="size-2.5" />
          </Link>
        ) : (
          <a key={index} href="#evidence" className="text-[10px] transition hover:opacity-80" style={{ color: C.accent }}>
            {ref.label}
          </a>
        ),
      )}
    </div>
  );
}

function DiagnosisCard({ item, accent }: { item: DiagnosisItem; accent: string }) {
  return (
    <div
      className="flex flex-col gap-1 rounded-lg px-2.5 py-2"
      style={{ background: C.surface, border: `1px solid ${C.border}`, borderLeft: `2px solid ${accent}` }}
    >
      <span className="text-[11px] leading-snug" style={{ color: C.fg3 }}>
        {item.statement}
      </span>
      <div className="flex flex-wrap items-center gap-2 text-[9px]" style={{ color: C.fg0 }}>
        <NeutralChip label={item.dimension} />
        <span>{item.comparator}</span>
        <span>confidence {item.confidence.toLowerCase()}</span>
      </div>
      <EvidenceLinks refs={item.evidence} />
    </div>
  );
}

function RecommendationCard({ rec, first }: { rec: Recommendation; first: boolean }) {
  return (
    <div
      className="flex flex-col gap-1.5 rounded-lg px-2.5 py-2"
      style={{
        background: C.surface,
        border: `1px solid ${rec.abstain ? 'rgba(240,173,78,0.3)' : first ? C.selectedBorder : C.border}`,
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <FlaskConical className="size-3" style={{ color: rec.abstain ? C.orange : C.accent }} />
        <span className="text-[11px] font-medium" style={{ color: C.fg4 }}>
          {rec.title}
        </span>
        <NeutralChip label={rec.category} />
        <NeutralChip label={`targets ${rec.target_dimension}`} />
        <span className="text-[9px]" style={{ color: C.fg0 }}>
          effort {rec.effort.toLowerCase()} · confidence {rec.confidence.toLowerCase()} · driven by {rec.comparator}
        </span>
      </div>
      <span className="text-[11px] leading-snug" style={{ color: C.fg2 }}>
        {rec.hypothesis}
      </span>
      <span className="text-[10px]" style={{ color: C.fg1 }}>
        Expected gain: {rec.expected_gain}
      </span>
      <span className="text-[10px]" style={{ color: C.fg1 }}>
        Validation: {rec.validation_plan}
      </span>
      <EvidenceLinks refs={rec.evidence_refs} />
    </div>
  );
}

export function DiagnosisSection({
  strengths,
  weaknesses,
  opportunities,
}: {
  strengths: DiagnosisItem[];
  weaknesses: DiagnosisItem[];
  opportunities: Recommendation[];
}) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium" style={{ color: C.fg3 }}>
        Diagnosis
      </span>
      <div className="grid items-start gap-3 lg:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.green }}>
            Strengths
          </span>
          {strengths.length ? (
            strengths.map((item, index) => <DiagnosisCard key={index} item={item} accent={C.green} />)
          ) : (
            <span className="text-[11px]" style={{ color: C.fg0 }}>
              No high-confidence strengths identified.
            </span>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.orange }}>
            Weaknesses
          </span>
          {weaknesses.length ? (
            weaknesses.map((item, index) => <DiagnosisCard key={index} item={item} accent={C.orange} />)
          ) : (
            <span className="text-[11px]" style={{ color: C.fg0 }}>
              No material weaknesses identified.
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide" style={{ color: C.accent }}>
          Opportunities — next experiment hypotheses
        </span>
        {opportunities.map((rec, index) => (
          <RecommendationCard key={index} rec={rec} first={index === 0} />
        ))}
      </div>
    </div>
  );
}
