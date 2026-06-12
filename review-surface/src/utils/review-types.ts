// Types for the derived review presentation model served by /api/review
// (built by scripts/derive-review.mjs).

export type DimensionKey =
  | 'task_fidelity'
  | 'scenario_fidelity'
  | 'workflow_discipline'
  | 'execution_reliability';

export type ConfidenceBand = 'High' | 'Medium' | 'Low' | 'Very Low';
export type AbsoluteStatus = 'Meets Scenario Bar' | 'Below Scenario Bar' | 'Unavailable';
export type DeltaBand = 'Ahead' | 'Parity' | 'Behind' | 'Inconclusive' | 'Unavailable';
export type DeltaSummary = DeltaBand | 'Mixed' | 'Benchmark';

export interface DimensionScore {
  score: number | null;
  caps_triggered: string[];
}

export interface ConfidenceInfo {
  score: number | null;
  band: ConfidenceBand;
  components: Array<{ name: string; value: number | null }>;
  spread: number | null;
  evidence_blocks: Array<{ block: string; status: string }>;
}

export interface DimensionDelta {
  delta: number | null;
  band: DeltaBand;
}

export interface BenchmarkDelta {
  is_benchmark: boolean;
  summary: DeltaSummary;
  dimensions: Partial<Record<DimensionKey, DimensionDelta>>;
  compatibility: 'compatible' | 'changed-baseline' | 'incompatible';
  compatibility_reason: string | null;
}

export interface RepresentativeInfo {
  experiment_id: string;
  dir: string;
  evaluation_profile: string | null;
  starter_fingerprint: string | null;
  created_at_utc: string | null;
  reason: string;
  below_minimum: boolean;
  scored_count: number;
  total_count: number;
  unresolved_unscored: number;
  repeats: number | null;
  run_ids: string[];
}

export interface EfficiencyAnchors {
  duration_sec: number | null;
  uncached_input_tokens: number | null;
  command_count: number | null;
  failed_command_count: number | null;
  verification_rounds: number | null;
}

export interface ReviewRow {
  review_id: string;
  scenario: string;
  revision: string;
  harness: string;
  model: string;
  agent_spec: string;
  synthetic: boolean;
  scenario_family: string;
  primary_scorer: string | null;
  scenario_fidelity_subtype: string;
  representative: RepresentativeInfo;
  dimensions: Record<DimensionKey, DimensionScore>;
  confidence: ConfidenceInfo;
  efficiency: EfficiencyAnchors;
  absolute_status: AbsoluteStatus;
  benchmark_delta: BenchmarkDelta | null;
  verdict: string;
}

export interface BoardBenchmark {
  status: 'pinned' | 'none' | 'pinned-missing';
  agent_spec: string | null;
  review_id: string | null;
}

export interface Board {
  scenario: string;
  revision: string;
  scenario_meta: {
    description: string | null;
    difficulty: string | null;
    category: string | null;
    timeout_sec: number | null;
  } | null;
  scenario_family: string;
  benchmark: BoardBenchmark;
  representative_rule: string;
  cohort: { size: number; meets: number; below: number; unavailable: number; low_confidence: number };
  freshness: string | null;
  rows: ReviewRow[];
}

export interface EvidenceRefLite {
  label: string;
  run_id?: string;
  block?: string;
}

export interface DiagnosisItem {
  statement: string;
  dimension: string;
  comparator: string;
  evidence: EvidenceRefLite[];
  confidence: ConfidenceBand;
}

export interface Recommendation {
  title: string;
  category: string;
  target_dimension: string;
  comparator: string;
  hypothesis: string;
  expected_gain: string;
  evidence_refs: EvidenceRefLite[];
  confidence: string;
  effort: string;
  validation_plan: string;
  abstain?: boolean;
}

export interface OutcomeProof {
  checks: Array<{
    name: string;
    kind: 'deterministic' | 'judge';
    pass_rate: number | null;
    median_score: number | null;
    missing_patterns: string[];
    evidence: string | null;
  }>;
  requirements: {
    total: number;
    presence_ratio: number | null;
    mapping_ratio: number | null;
    missing_ids: string[];
  } | null;
}

export interface ImplementationProof {
  files: Array<{ path: string; runs_touched: number }>;
  run_count: number;
}

export interface VerificationProof {
  first_pass_rate: number | null;
  gate_failures: Array<{ name: string; failures: number; last_detail: string | null }>;
  required_command_misses: string[];
  gates_per_run: number;
}

export interface VisualRegionEvidence {
  name: string;
  median_score: number | null;
  pass_rate: number | null;
  threshold: number | null;
  actual_path: string | null;
  reference_path: string | null;
  diff_path: string | null;
}

export interface VisualProof {
  anchor_run: string;
  similarity_median: number | null;
  threshold_pass_rate: number | null;
  capture_failures: number;
  reference_path: string | null;
  actual_path: string | null;
  diff_path: string | null;
  regions: VisualRegionEvidence[];
}

export interface EvidenceSide {
  anchor: { run_id: string; atypical: boolean } | null;
  visual: VisualProof | null;
  outcome: OutcomeProof | null;
  implementation: ImplementationProof | null;
  verification: VerificationProof | null;
}

export interface ReviewEvidence {
  availability: Array<{ block: string; status: string }>;
  current: EvidenceSide;
  benchmark: EvidenceSide | null;
}

export interface ChangeContext {
  previous_experiment_id: string | null;
  previous_review_id: string | null;
  previous_revision?: string;
  changes: Array<{ category: string; detail: string; comparability_warnings: string[] }>;
  summary: string;
}

export interface RunConsistencyRow {
  run_id: string;
  scored: boolean;
  valid: boolean;
  duration_sec: number | null;
  uncached_input_tokens: number | null;
  dimensions: Record<DimensionKey, number | null>;
  issues: number;
  outlier: boolean;
  outlier_reasons: string[];
}

export interface ReviewDetail extends ReviewRow {
  benchmark: BoardBenchmark & {
    dimensions: Record<DimensionKey, DimensionScore> | null;
    confidence_band: ConfidenceBand | null;
  };
  primary_strength: string;
  primary_weakness: string;
  change_context: ChangeContext;
  evidence: ReviewEvidence;
  diagnosis: {
    strengths: DiagnosisItem[];
    weaknesses: DiagnosisItem[];
    opportunities: Recommendation[];
  };
  run_consistency: RunConsistencyRow[];
}

export interface ReviewResponse {
  boards: Board[];
  reviews: Record<string, ReviewDetail>;
}

export const DIMENSION_LABELS: Record<DimensionKey, string> = {
  task_fidelity: 'Task Fidelity',
  scenario_fidelity: 'Scenario Fidelity',
  workflow_discipline: 'Workflow Discipline',
  execution_reliability: 'Execution Reliability',
};

export const DIMENSION_KEYS: DimensionKey[] = [
  'task_fidelity',
  'scenario_fidelity',
  'workflow_discipline',
  'execution_reliability',
];
