// Shared verdict renderings: tier pills, score bars, and concise id chips.
// Every score the surface shows should pass through one of these so the
// good/bad vocabulary stays consistent.
import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { C } from '@/utils/colors';
import { scoreTier, type Tier } from '@/utils/verdict';

/** Named tier pill, e.g. ● Strong. */
export function TierPill({ tier, detail, size = 'sm' }: { tier: Tier; detail?: string; size?: 'sm' | 'lg' }) {
  const lg = size === 'lg';
  return (
    <span
      title={detail ?? tier.blurb}
      className={`inline-flex items-center rounded-full font-medium ${lg ? 'gap-2 px-2.5 py-0.5 text-xs' : 'gap-1.5 px-2 py-px text-[10px]'}`}
      style={{ color: tier.color, background: `${tier.color}14`, border: `1px solid ${tier.color}40` }}
    >
      <span className={`rounded-full ${lg ? 'size-2' : 'size-1.5'}`} style={{ background: tier.color }} />
      {tier.label}
    </span>
  );
}

/** Score verdict: tier pill plus a small 0..1 bar with the number tucked after it. */
export function ScoreVerdict({
  score,
  unscored = false,
  detail,
  barWidth = 64,
}: {
  score: number | null | undefined;
  unscored?: boolean;
  detail?: string;
  barWidth?: number;
}) {
  const tier = scoreTier(unscored ? null : score);
  return (
    <span className="inline-flex items-center gap-2" title={detail ?? tier.blurb}>
      <TierPill tier={tier} detail={detail} />
      {score != null && !unscored && <ScoreBar score={score} width={barWidth} color={tier.color} />}
      {score != null && !unscored && (
        <span className="num text-[10px]" style={{ color: C.fg1 }}>
          {score.toFixed(2)}
        </span>
      )}
    </span>
  );
}

/** Minimal horizontal 0..1 bar. */
export function ScoreBar({ score, width = 64, color, height = 4 }: { score: number; width?: number; color?: string; height?: number }) {
  const tier = scoreTier(score);
  return (
    <span
      className="inline-block shrink-0 overflow-hidden rounded-full align-middle"
      style={{ width, height, background: 'rgba(255,255,255,0.07)' }}
      aria-hidden
    >
      <span
        className="block h-full rounded-full"
        style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%`, background: color ?? tier.color }}
      />
    </span>
  );
}

/** Concise conceptual label for a long id: shows the label, keeps the full id in a tooltip with one-click copy. */
export function IdChip({ id, label, color = C.fg1 }: { id: string; label: string; color?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span
      className="group/id num inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px]"
      style={{ color, background: 'rgba(255,255,255,0.04)', border: `1px solid ${C.border}` }}
      title={id}
    >
      {label}
      <button
        className="opacity-0 transition group-hover/id:opacity-100"
        title={`Copy full id: ${id}`}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          void navigator.clipboard.writeText(id);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? (
          <Check className="size-2.5" style={{ color: C.green }} />
        ) : (
          <Copy className="size-2.5" style={{ color: C.fg0 }} />
        )}
      </button>
    </span>
  );
}
