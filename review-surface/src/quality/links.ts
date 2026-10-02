// Joins between the run review and the Quality element. A run review record and
// a Quality run describe the same trial record: the run review shows it as
// artifact_paths.run_json (repo-relative), the Quality projection as
// `<jobs-root name>/<job dir>/trial-records/<trial>.json`, so a path-suffix match
// is the join. Deep links carry the experiment (name + lock prefix) and problem
// for the experiment view, and the drill-down selection for the run view.
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { qualityApi } from '@/quality/api';
import type { Selection } from '@/quality/components/QualityStrip';
import {
  DRILLDOWN_COMPONENTS,
  type DrilldownComponent,
  type QualityExperiment,
  type QualityRunSummary,
} from '@/quality/types';

const LOCK_PREFIX = 12;

export interface QualityTarget {
  experiment: string;
  lock: string | null;
  problem: string;
}

export function qualityExperimentHref(target: QualityTarget): string {
  const params = new URLSearchParams({ experiment: target.experiment });
  if (target.lock) params.set('lock', target.lock.slice(0, LOCK_PREFIX));
  params.set('problem', target.problem);
  return `/quality?${params}`;
}

export function qualityRunHref(runId: string, selection?: Selection | null): string {
  const base = `/quality/runs/${encodeURIComponent(runId)}`;
  if (!selection) return base;
  const params = new URLSearchParams({ component: selection.component });
  if (selection.checkpoint) params.set('checkpoint', selection.checkpoint);
  return `${base}?${params}`;
}

/** The drill-down a /quality/runs/:runId?component=&checkpoint= link asks for, if valid. */
export function selectionFromParams(params: URLSearchParams): Selection | null {
  const component = params.get('component');
  if (!component || !(DRILLDOWN_COMPONENTS as readonly string[]).includes(component)) return null;
  return { component: component as DrilldownComponent, checkpoint: params.get('checkpoint') };
}

/** Whether an experiment matches a deep link's experiment name and (optional) lock prefix. */
export function matchesTarget(
  item: { experiment: string; lock_sha256: string | null },
  experiment: string | null,
  lock: string | null,
): boolean {
  if (!experiment || item.experiment !== experiment) return false;
  return !lock || (item.lock_sha256 ?? '').startsWith(lock);
}

function joins(runJson: string, trialRecord: string): boolean {
  return runJson === trialRecord || runJson.endsWith(`/${trialRecord}`);
}

/** Quality runs and experiments, shared (same query keys) with the Quality pages. */
export function useQualityIndex() {
  const runs = useQuery({ queryKey: ['quality-runs'], queryFn: qualityApi.runs });
  const experiments = useQuery({ queryKey: ['quality-experiments'], queryFn: qualityApi.experiments });
  return useMemo(() => {
    const runList = runs.data?.runs ?? [];
    const experimentList = experiments.data?.experiments ?? [];

    /** The Quality run of a run review's trial record; null when absent or ambiguous. */
    const runFor = (runJson: string | null | undefined): QualityRunSummary | null => {
      if (!runJson) return null;
      const matches = runList.filter((run) => joins(runJson, run.trial_record));
      return matches.length === 1 ? matches[0] : null;
    };

    const experimentOf = (run: QualityRunSummary): QualityExperiment | null =>
      experimentList.find(
        (item) =>
          item.experiment === run.experiment &&
          item.jobs.includes(run.job) &&
          item.problems.some((problem) => problem.run_ids.includes(run.id)),
      ) ?? null;

    const targetOf = (run: QualityRunSummary): QualityTarget => ({
      experiment: run.experiment,
      lock: experimentOf(run)?.lock_sha256 ?? null,
      problem: run.problem,
    });

    /** Distinct experiment views covering the given run reviews' trials. */
    const targetsFor = (runJsons: Array<string | null | undefined>): QualityTarget[] => {
      const seen = new Map<string, QualityTarget>();
      for (const runJson of runJsons) {
        const run = runFor(runJson);
        if (!run) continue;
        const target = targetOf(run);
        seen.set(`${target.experiment}@${target.lock}:${target.problem}`, target);
      }
      return [...seen.values()];
    };

    return {
      loaded: runs.isSuccess && experiments.isSuccess,
      runFor,
      targetOf,
      targetsFor,
      experiments: experimentList,
    };
  }, [runs.data, runs.isSuccess, experiments.data, experiments.isSuccess]);
}
