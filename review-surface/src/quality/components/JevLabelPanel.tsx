// DORMANT since 2026-10-04 (Jev parked): not rendered; QualityRunPage shows
// JevHistoryPanel instead. Kept intact for a future decision to un-park Jev.
// Reviewer calibration labels for this run's Jev decisions (plan K4). A label
// saved here is pending until `make jev-export-labels` exports it for
// `raidar jev import-labels`; a label already in the audit is shown locked.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { C } from '@/utils/colors';
import { qualityApi } from '@/quality/api';
import { JEV_LABELS, type JevDecisionItem, type JevLabel } from '@/quality/types';

const LABEL_TEXT: Record<JevLabel, string> = {
  correct: 'Correct',
  false_positive: 'False positive',
  false_negative: 'False negative',
  uncertain: 'Uncertain',
};

export function JevLabelPanel({ runId, decisions }: { runId: string; decisions: JevDecisionItem[] }) {
  const queryClient = useQueryClient();
  const labels = useQuery({ queryKey: ['quality-labels', runId], queryFn: () => qualityApi.labels(runId) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['quality-labels', runId] });
  const create = useMutation({ mutationFn: qualityApi.createLabel, onSuccess: refresh });
  const remove = useMutation({ mutationFn: qualityApi.deleteLabel, onSuccess: refresh });
  const pending = new Map((labels.data ?? []).map((item) => [`${item.policy}/${item.request_id}`, item]));

  if (decisions.length === 0) {
    return (
      <p className="text-[11px]" style={{ color: C.fg1 }}>
        No Jev decisions were audited for this trial.
      </p>
    );
  }
  return (
    <ul data-testid="jev-decisions">
      {decisions.map((decision) => {
        const saved = pending.get(`${decision.policy}/${decision.request_id}`);
        return (
          <li
            key={decision.request_id}
            data-testid="jev-decision"
            className="flex flex-wrap items-center gap-2 py-1.5"
            style={{ borderBottom: `1px solid ${C.rowBorder}` }}
          >
            <code className="min-w-0 flex-1 truncate text-[11px]" style={{ color: C.fg3 }} title={decision.request_id}>
              {decision.subject}
            </code>
            <span className="text-[10px]" style={{ color: C.fg1 }}>
              {decision.policy} · {decision.stage} · {decision.verdict}
            </span>
            {decision.label ? (
              <span className="text-[10px] font-semibold" style={{ color: C.fg2 }} title="Imported into the audit">
                {LABEL_TEXT[decision.label]} (imported)
              </span>
            ) : (
              <span role="group" aria-label={`Label decision ${decision.request_id}`} className="inline-flex gap-1">
                {JEV_LABELS.map((label) => {
                  const active = saved?.label === label;
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={active}
                      disabled={create.isPending}
                      className="q-focus rounded px-1.5 py-0.5 text-[10px] transition"
                      style={{
                        color: active ? C.buttonFg : C.fg2,
                        background: active ? C.button : C.subtleStrong,
                        border: `1px solid ${C.borderLight}`,
                      }}
                      onClick={() =>
                        active && saved
                          ? remove.mutate(saved.id)
                          : create.mutate({ run_id: runId, request_id: decision.request_id, policy: decision.policy, label })
                      }
                    >
                      {LABEL_TEXT[label]}
                    </button>
                  );
                })}
              </span>
            )}
          </li>
        );
      })}
      {(create.isError || remove.isError) && (
        <li className="pt-1 text-[10px]" style={{ color: C.red }}>
          The label was not saved. Check that the review server is running, then try again.
        </li>
      )}
    </ul>
  );
}
