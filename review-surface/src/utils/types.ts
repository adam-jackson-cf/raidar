export type SpanType = 'TRACE' | 'AGENT_ROOT' | 'LLM_GENERATION' | 'TOOL_CALL' | 'INTERNAL';
export type SpanStatus = 'OK' | 'ERROR' | 'UNSET';
export type AnnotationKind = 'issue' | 'good' | 'note';

export interface RunRecord {
  id: string;
  name: string;
  scenario: string;
  revision: string;
  harness: string;
  model: string;
  agent_spec: string;
  experiment_id: string;
  started_at: number;
  duration_ms: number;
  status: SpanStatus;
  span_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  composite_score: number | null;
  quality_score: number | null;
  diagnostic_score: number | null;
  unscored: boolean;
  unscored_reasons: string[];
  valid: boolean;
  synthetic: boolean;
  metric_scores?: Array<{
    metric_id: string;
    score: number;
    passed: boolean;
  }>;
  finding_counts: { issue: number; good: number; note: number };
  issue_categories: Record<string, number>;
  failed_gates: string[];
  artifact_paths: { run_json: string; findings_json: string | null };
}

export interface Span {
  id: string;
  run_id: string;
  parent_span_id: string | null;
  name: string;
  span_type: SpanType;
  status: SpanStatus;
  start_time_ms: number | null;
  end_time_ms: number | null;
  duration_ms: number | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  input_payload: string | null;
  output_payload: string | null;
  attributes: string | null;
}

export interface FindingEvidenceRef {
  source: string;
  reference: string;
  detail: string;
}

export interface Annotation {
  id: string;
  run_id: string;
  span_id: string | null;
  kind: AnnotationKind;
  note: string | null;
  source: 'raidar' | 'user';
  created_at: number;
  category: string;
  evidence: FindingEvidenceRef[];
}

export interface RunDetail {
  run: RunRecord;
  spans: Span[];
  annotations: Annotation[];
}

export interface RunOutline {
  run: RunRecord;
  summary: {
    span_type_counts: Record<string, number>;
    tool_calls: {
      total: number;
      by_name: Array<{
        name: string;
        count: number;
        errors: number;
        example: {
          span_id: string;
          status: SpanStatus;
          input_preview: string;
          output_preview: string;
        } | null;
      }>;
    };
  };
  errors: Array<{ span_id: string; name: string; ts: number | null; first_line_of_output: string }>;
  annotations: Annotation[];
}

export interface SearchMatch {
  span_id: string;
  span_name: string;
  scope: string;
  match_range: [number, number];
  snippet: string;
  payload_total_chars: number;
}

export interface StatBlock {
  mean?: number;
  median?: number;
  stddev?: number;
  min?: number;
  max?: number;
}

export interface ExperimentRecord {
  experiment_id: string;
  dir: string;
  scenario: string | null;
  revision: string | null;
  harness: string | null;
  model: string | null;
  agent_spec: string;
  synthetic: boolean;
  repeats: number | null;
  scenario_meta: {
    description: string | null;
    difficulty: string | null;
    category: string | null;
    timeout_sec: number | null;
  } | null;
  aggregate: {
    run_count_total?: number;
    run_count_scored?: number;
    unscored_count?: number;
    validity_rate?: number;
    performance_pass_rate?: number;
    composite_score?: StatBlock;
    quality_score?: StatBlock;
    duration_sec?: StatBlock;
    uncached_input_tokens?: StatBlock;
    metric_outcomes?: Record<
      string,
      { pass_count: number; fail_count: number; sample_size: number; pass_rate: number; mean_score: number }
    >;
    scorer_outcomes?: Record<string, { sample_size: number; mean_score: number }>;
  };
  sample: { sample_adequacy?: number; minimum_met?: boolean; preferred_met?: boolean; sample_class?: string };
  rerun: { target_met?: boolean; unresolved_unscored_count?: number };
  findings: Array<{
    id: string;
    kind: AnnotationKind;
    category: string;
    title: string;
    detail: string;
    evidence: FindingEvidenceRef[];
  }>;
  created_at_utc: string | null;
  run_ids: string[];
}

export interface DiffLine {
  type: 'context' | 'added' | 'removed';
  text: string;
}

export interface FileDiff {
  path: string;
  diff: { added: number; removed: number; truncated: boolean; lines: DiffLine[] };
}

export interface RevisionDiff {
  key: string;
  scenario: string;
  from_revision: string;
  to_revision: string;
  summary: string[];
  comparable_warnings: string[];
  sections?: Record<string, FileDiff>;
  files: { scenario: FileDiff; prompt: FileDiff };
}

export interface ExperimentsResponse {
  experiments: ExperimentRecord[];
  revision_diffs: RevisionDiff[];
}
