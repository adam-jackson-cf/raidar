// Quality projection contract (raidar-quality-projection, schema_version 1),
// emitted by orchestrator/src/raidar/review/quality/models.py.

export const COMPONENTS = ['C', 'V', 'E', 'H', 'R', 'G', 'I', 'S'] as const;
export type Component = (typeof COMPONENTS)[number];
export const DRILLDOWN_COMPONENTS = ['V', 'E', 'H', 'R', 'G'] as const;
export type DrilldownComponent = (typeof DRILLDOWN_COMPONENTS)[number];

export type CellStatus =
  | 'ok'
  | 'not_applicable'
  | 'ineligible'
  | 'insufficient_evidence'
  | 'unsupported'
  | 'unavailable'
  | 'error'
  | 'unassessed'
  | 'truncated';

export interface Cell {
  status: CellStatus;
  value: number | null;
  reason: string | null;
}

export interface PolicyInfo {
  ref: string;
  source: 'lock' | 'default_by_k';
  inertia_weights: Record<string, number>;
  not_applicable: string[];
}

export interface CheckpointSignals {
  lint_findings: number | null;
  production_loc: number | null;
  lint_per_kloc: number | null;
  high_cc_count: number | null;
  high_cc_mass_share: number | null;
  high_cc_function_lines: number[];
}

export interface CheckpointRow {
  checkpoint: string;
  state: string;
  cells: Record<Component, Cell>;
  signals: CheckpointSignals;
}

export interface EvidenceItem {
  component: DrilldownComponent;
  subject: string;
  checkpoint: string;
  path: string;
  lines: Array<[number, number]>;
  kind: string;
  message: string;
  sha256: string;
  snapshot_path: string | null;
}

export interface ExclusionItem {
  checkpoint: string;
  path: string;
  reason: string;
}

export type JevLabel = 'correct' | 'false_positive' | 'false_negative' | 'uncertain';
export const JEV_LABELS: JevLabel[] = ['correct', 'false_positive', 'false_negative', 'uncertain'];

export interface JevDecisionItem {
  request_id: string;
  policy: string;
  subject: string;
  stage: string;
  verdict: string;
  rule: string | null;
  label: JevLabel | null;
}

export interface QualityRunSummary {
  id: string;
  experiment: string;
  job: string;
  trial: string;
  arm: string;
  problem: string;
  attempt: number;
  valid: boolean;
  checkpoints: number;
  truncated_to: number | null;
  score: Cell;
  sources: Record<string, string | null>;
  /** `<jobs-root name>/<job dir>/trial-records/<trial>.json`: joins the run review's artifact_paths.run_json. */
  trial_record: string;
}

/** Rule families of the gate's blocking findings (raidar.scorers.gate_effort.families). */
export const GATE_FAMILIES = [
  'complexity',
  'typing',
  'defects',
  'structure',
  'style',
  'naming',
  'tests',
  'suppressions',
  'other',
] as const;
export type GateFamily = (typeof GATE_FAMILIES)[number];

export interface GateRound {
  run: number;
  after: string;
  verdict: 'passed' | 'refused' | 'timed_out' | 'error';
  blocking: number | null;
  listed: number;
  /** Blocking findings per family; null when the stored files do not name them all. */
  families: Record<GateFamily, number> | null;
  source: string;
  summary: string;
  log_path: string | null;
  duration_s: number | null;
}

export interface GateStep {
  checkpoint: string;
  termination: string | null;
  cells: Record<string, Cell>;
  families: Record<GateFamily, Cell>;
  first_run_families: Record<GateFamily, Cell>;
  rounds: GateRound[];
}

/** A gated trial's commit-gate effort (gate-effort@1/@2); absent (null) for an ungated trial. */
export interface GateView {
  source: string | null;
  /** The gate-effort version the cells come from (null: unassessed). */
  scorer?: string | null;
  /** A shadow gate: recorded at each step's end, never enforced (fix outcomes not applicable). */
  shadow?: boolean;
  steps: GateStep[];
  totals: Record<string, Cell>;
  notes: string[];
}

export interface QualityRunDetail {
  run: QualityRunSummary;
  policy: PolicyInfo;
  rows: CheckpointRow[];
  problem: Record<Component, Cell>;
  policy_reasons: string[];
  evidence: EvidenceItem[];
  exclusions: ExclusionItem[];
  jev_decisions: JevDecisionItem[];
  notes: string[];
  gate?: GateView | null;
}

export interface CurvePoint {
  checkpoint: string;
  n: number;
  mean: number | null;
  sd: number | null;
  low: number | null;
  high: number | null;
  interval_method: string | null;
}

export type Verdict = 'improved' | 'worse' | 'inconclusive' | 'insufficient';

export interface PairedDelta {
  component: Component;
  arm: string;
  n: number;
  mean: number | null;
  sd: number | null;
  ci: [number, number] | null;
  verdict: Verdict;
  excluded: string[];
}

export interface Distribution {
  arm: string;
  n: number;
  values: number[];
  quantiles: [number, number, number, number, number] | null;
}

export interface SignalDistributions {
  signal: string;
  label: string;
  unit: string;
  source: string;
  arms: Distribution[];
}

export interface ScatterPoint {
  checkpoint: string;
  a: number;
  b: number;
  n_a: number;
  n_b: number;
}

export interface HeadToHeadSignal {
  signal: string;
  label: string;
  axis: string;
  engine: string;
  points: ScatterPoint[];
  excluded: string[];
}

export interface HeadToHead {
  arm_a: string;
  arm_b: string;
  aggregation: 'median_across_attempts';
  signals: HeadToHeadSignal[];
}

export interface Correlation {
  a: string;
  b: string;
  r: number | null;
  reason: string | null;
}

export interface Sensitivity {
  n: number;
  weights: Record<string, number>;
  ablation: Record<string, number>;
  correlations: Correlation[];
  excluded: string[];
}

export interface ProblemQuality {
  problem: string;
  checkpoints: string[];
  policy: PolicyInfo;
  baseline_arm: string | null;
  arms: string[];
  run_ids: string[];
  curves: Record<string, Record<Component, CurvePoint[]>>;
  paired: PairedDelta[];
  distributions: SignalDistributions[];
  head_to_head: HeadToHead | null;
  sensitivity: Sensitivity;
  excluded: string[];
  notes: string[];
}

export interface QualityExperiment {
  experiment: string;
  lock_sha256: string | null;
  jobs: string[];
  problems: ProblemQuality[];
  /** The pre-registered primary outcome, as the analysis report labels it. */
  primary_outcome?: string | null;
  primary_source?: string | null;
  /** The raw SCB counterpart an errata-applied primary is reported beside. */
  primary_raw?: string | null;
}

export interface QualityRunsResponse {
  generated_from?: string;
  synthetic?: boolean;
  runs: QualityRunSummary[];
}

export interface QualityExperimentsResponse {
  generated_from?: string;
  synthetic?: boolean;
  experiments: QualityExperiment[];
}

export interface JevLabelRecord {
  id: string;
  run_id: string;
  request_id: string;
  policy: string;
  label: JevLabel;
  note: string | null;
  source: 'user';
  category: 'jev_label';
  created_at: number;
}
