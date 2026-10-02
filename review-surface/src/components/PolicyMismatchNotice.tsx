// Headline-policy mismatch: one notice, one wording, wherever a headline score
// is compared or shown. The projection marks every experiment of a compared
// family (one problem) that mixes headline
// policies with a `headline-policy-mismatch` finding; the Quality element adds
// the policies its own experiments score the same problem under.
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import { api } from '@/api/client';
import { C } from '@/utils/colors';
import type { ExperimentRecord } from '@/utils/types';
import type { QualityExperiment } from '@/quality/types';

export const MISMATCH_CATEGORY = 'headline-policy-mismatch';
export const MISMATCH_TITLE = 'Headline policies differ in this comparison';

export interface PolicyMismatch {
  /** policy ref -> experiments scored under it */
  policies: Array<{ policy: string; experiments: string[] }>;
}

/** The mismatch the projection recorded on a family's experiments, if any. */
export function familyMismatch(experiments: ExperimentRecord[]): PolicyMismatch | null {
  const finding = experiments
    .flatMap((exp) => exp.findings)
    .find((item) => item.category === MISMATCH_CATEGORY);
  if (!finding) return null;
  return {
    policies: finding.evidence
      .filter((ref) => ref.source === 'headline_policy')
      .map((ref) => ({ policy: ref.reference, experiments: ref.detail.split(', ').filter(Boolean) })),
  };
}

/** Quality experiments scoring `problem` under more than one headline policy. */
export function qualityMismatch(experiments: QualityExperiment[], problem: string): PolicyMismatch | null {
  const byPolicy = new Map<string, string[]>();
  for (const item of experiments) {
    for (const entry of item.problems) {
      if (entry.problem !== problem) continue;
      const list = byPolicy.get(entry.policy.ref) ?? [];
      list.push(`${item.experiment} · lock ${item.lock_sha256?.slice(0, 12) ?? 'unknown'}`);
      byPolicy.set(entry.policy.ref, list);
    }
  }
  if (byPolicy.size < 2) return null;
  return { policies: [...byPolicy.entries()].sort().map(([policy, list]) => ({ policy, experiments: list })) };
}

function merge(a: PolicyMismatch | null, b: PolicyMismatch | null): PolicyMismatch | null {
  if (!a) return b;
  if (!b) return a;
  const byPolicy = new Map<string, Set<string>>();
  for (const { policy, experiments } of [...a.policies, ...b.policies]) {
    const set = byPolicy.get(policy) ?? new Set<string>();
    experiments.forEach((id) => set.add(id));
    byPolicy.set(policy, set);
  }
  return { policies: [...byPolicy.entries()].sort().map(([policy, ids]) => ({ policy, experiments: [...ids] })) };
}

/** Mismatch for the family (problem id) a run review or Quality view belongs to. */
export function usePolicyMismatch(problem: string | null | undefined, quality?: QualityExperiment[]) {
  const experiments = useQuery({ queryKey: ['experiments'], queryFn: api.experiments });
  return useMemo(() => {
    if (!problem) return null;
    const family = (experiments.data?.experiments ?? []).filter((exp) => exp.problem === problem);
    return merge(familyMismatch(family), quality ? qualityMismatch(quality, problem) : null);
  }, [experiments.data, problem, quality]);
}

export function PolicyMismatchNotice({ mismatch }: { mismatch: PolicyMismatch | null }) {
  if (!mismatch) return null;
  return (
    <div
      role="note"
      data-testid="policy-mismatch"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-2.5 py-1.5 text-[11px]"
      style={{ color: C.fg3, background: `${C.orange}12`, border: `1px solid ${C.orange}40` }}
    >
      <TriangleAlert className="size-3.5 shrink-0" style={{ color: C.orange }} aria-hidden />
      <span className="font-medium" style={{ color: C.orange }}>
        {MISMATCH_TITLE}
      </span>
      <span style={{ color: C.fg1 }}>scores under different policies are not comparable; compare within one</span>
      <span className="flex flex-wrap gap-1">
        {mismatch.policies.map(({ policy, experiments }) => (
          <span
            key={policy}
            className="num rounded px-1.5 py-px text-[10px]"
            style={{ color: C.fg2, border: `1px solid ${C.borderLight}` }}
            title={experiments.join('\n')}
          >
            {policy}
            <span className="ml-1" style={{ color: C.fg0 }}>
              ×{experiments.length}
            </span>
          </span>
        ))}
      </span>
    </div>
  );
}

/** The notice for one family (problem id), for places that know only the family. */
export function FamilyPolicyMismatch({ problem, quality }: { problem: string | null | undefined; quality?: QualityExperiment[] }) {
  return <PolicyMismatchNotice mismatch={usePolicyMismatch(problem, quality)} />;
}
