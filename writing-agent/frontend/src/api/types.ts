// Payload types mirroring writing_agent.webapp endpoints.

export interface DraftMeta {
  slug: string;
  mtime: number;
}

export interface VersionMeta {
  file: string;
  mtime: number;
  source: string;
}

export interface BlockFailure {
  block_index: number;
  reasons: string[];
}

export interface LintMetrics {
  banned?: { block_index: number; pattern: string; count: number }[];
  burstiness?: { block_index: number; mean: number; std_dev: number; variance: number }[];
  transitions?: {
    doc_density: number;
    transition_block_indices: number[];
    em_dash_per_1k: Record<string, number>;
    reason: string | null;
  };
  deep_syntax?: { block_index: number; counts: Record<string, number>; total: number }[];
  redundancy?: { block_a: number; block_b: number; jaccard: number; shared: string[] }[];
}

export interface LintReport {
  passed: boolean;
  flagged_blocks: number[];
  failures: BlockFailure[];
  metrics: LintMetrics;
  scorecard: string;
}

export interface SurprisalBlock {
  block_index: number;
  overlap: number | null;
  mean_bits: number | null;
  agreement: number | null;
  corroboration: number | null;
  n_gen_tokens: number;
  failed: boolean;
  reason: string | null;
}

export interface BitsBurstiness {
  bits_sd: number;
  sd_min: number;
  failed: boolean;
}

export interface SurprisalReport {
  skipped: boolean;
  warning: string | null;
  blocks: SurprisalBlock[];
  flagged_blocks: number[];
  bits_burstiness: BitsBurstiness | null;
}

export interface JudgeAxes {
  over_explains: number;
  linear_timeline: number;
  tidy_resolution: number;
  generic_abstraction: number;
  reads_like_ai: number;
  notes: string;
}

export interface ShapeMetrics {
  words: number;
  temporal_jumps_per_1k: number;
  retro_explanations_per_1k: number;
  concessions_per_1k: number;
  unresolved_markers: number;
  incident_anchors_per_1k: number;
  reader_address_per_1k: number;
  numbers_per_1k: number;
  fragments_per_1k: number;
  rhetorical_questions: number;
  summary_hit_blocks: Record<string, string[]>;
  top5_content_share: number;
  section_count: number;
  section_length_sd: number;
}

export interface DiscourseFailure {
  block_index: number | null;
  reason: string;
}

export interface SemanticReport {
  skipped: boolean;
  warning?: string | null;
  steps: number[];
  step_min: number | null;
  step_max: number | null;
  step_mean: number | null;
  step_sd?: number | null;
  dip_pair: number[] | null;
  failed: boolean;
  reason: string | null;
  flagged_blocks: number[];
  detail?: {
    block_index: number;
    n_sentences: number;
    sentence_step_min: number;
    sentence_step_mean: number;
  } | null;
}

export interface DiscourseReport {
  metrics: ShapeMetrics;
  judge: JudgeAxes | null;
  failures: DiscourseFailure[];
  flagged_blocks: number[];
  passed: boolean;
  semantic?: SemanticReport;
}

export interface CompareDoc {
  label: string;
  group: "current" | "draft" | "human" | "ai";
  x: number;
  y: number;
  axes: Record<string, number>;
  human_score: number;
  rarity: number;
}

export interface Authorship {
  human_dist: number;
  ai_dist: number;
  ratio: number;
  verdict: "human-side" | "machine-side";
}

export interface CompareResponse {
  axes_names: string[];
  docs: CompareDoc[];
  judge: JudgeAxes | null;
  authorship: Authorship | null;
}

export interface JobEvent {
  kind: "stage" | "trace" | "done";
  stage: string;
  elapsed: number;
  event?: string;
  error?: string | null;
  result?: {
    slug: string;
    scorecard: string;
    flagged_blocks: number[];
  } | null;
}

export interface FixResponse {
  changed: boolean;
  markdown: string;
  reasons: Record<string, string[]>;
}

export interface ReviseResponse {
  markdown: string;
  scope: "block" | "selection" | "document";
  block_index?: number;
}

export interface ChatEntry {
  role: "you" | "agent" | "error" | "hint";
  text: string;
  at: number;
}
