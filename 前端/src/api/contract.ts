export type VerdictStatus = 'PASSED' | 'DEGRADED' | 'BLOATED' | 'VULNERABLE' | 'FAILED';
export type CompressionMode = 'balanced' | 'lossless' | 'aggressive';
export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';
export type ContentTypeHint = 'text' | 'json' | 'code' | 'log' | 'diff' | 'search';
export type SecurityVerdict = 'SAFE' | 'VULNERABLE';
export type AuditStatus = 'PASS' | 'FAIL';

export interface HealthResponse {
  status: string;
  upstream_version: string;
  upstream_commit: string;
  tiktoken_enabled: boolean;
  active_stages: number;
  rewind_entries: number;
  mode: string;
}

export interface PresetItem {
  id: string;
  name: string;
  subtitle: string;
  role: MessageRole;
  content_type: ContentTypeHint;
  mode: CompressionMode;
  target_budget_tokens: number;
  redact_secrets: boolean;
  query: string;
  text: string;
  description: string;
}

export interface PresetsResponse {
  presets: PresetItem[];
}

export interface CompressCompareRequest {
  preset_id?: string;
  text: string;
  role: MessageRole;
  content_type: ContentTypeHint;
  query?: string;
  target_budget_tokens?: number;
  redact_secrets: boolean;
  deterministic_seed: number;
  mode: CompressionMode;
}

export interface StageStepMetrics {
  name: string;
  order: number;
  skipped: boolean;
  rolled_back: boolean;
  original_tokens: number;
  compressed_tokens: number;
  delta_tokens: number;
  timing_ms: number;
  note: string;
}

export interface RewindMarkerRef {
  hash_id: string;
  original_count: number;
  compressed_count: number;
  original_tokens: number;
}

export interface EngineRunOutcome {
  engine: 'upstream' | 'enhanced';
  original_tokens: number;
  compressed_tokens: number;
  reduction_pct: number;
  latency_ms: number;
  prefix_hash: string;
  prefix_deterministic: boolean;
  secret_leaks_count: number;
  needle_retention_pct: number;
  structure_preserved: boolean;
  verdict: VerdictStatus;
  stages: StageStepMetrics[];
  compressed_text: string;
  rewind_markers: RewindMarkerRef[];
  notes: string[];
}

export interface CompressCompareResponse {
  preset_id: string;
  mode: CompressionMode;
  upstream: EngineRunOutcome;
  enhanced: EngineRunOutcome;
  token_delta: number;
  speedup_ratio: number;
}

export interface ChatTurnMessage {
  role: MessageRole;
  content: string;
  name?: string;
  tool_call_id?: string;
}

export interface KvCacheSimulateRequest {
  runs: number;
  messages: ChatTurnMessage[];
  keep_recent: number;
  max_tokens_per_result: number;
  query?: string;
  redact_secrets: boolean;
}

export interface KvCacheTrackResult {
  engine: 'upstream' | 'enhanced';
  runs: number;
  unique_hashes: number;
  prefix_hit_rate_pct: number;
  hashes: string[];
  tail_exception_retained: boolean;
  indexed_placeholders_ok: boolean;
  secret_leaks_count: number;
  compressed_tokens: number;
  original_tokens: number;
  verdict: VerdictStatus;
  sample_output: string;
}

export interface KvCacheSimulateResponse {
  upstream: KvCacheTrackResult;
  enhanced: KvCacheTrackResult;
  hit_rate_gain_pct: number;
}

export interface RewindRetrieveRequest {
  hash_id: string;
}

export interface RewindRetrieveResponse {
  found: boolean;
  hash_id: string;
  original_text: string | null;
  original_tokens: number;
  compressed_tokens: number;
  evicted_or_expired: boolean;
}

export interface SecurityRedactRequest {
  text: string;
  redact_secrets: boolean;
  index_placeholders: boolean;
}

export interface SecretFinding {
  kind: string;
  placeholder: string;
  masked_preview: string;
  count: number;
}

export interface SecurityTrackResult {
  verdict: SecurityVerdict;
  raw_secret_echoed: boolean;
  findings: SecretFinding[];
  stabilized_text: string;
  prefix_hash: string;
  expansion_tokens: number;
}

export interface SecurityRedactResponse {
  upstream: SecurityTrackResult;
  enhanced: SecurityTrackResult;
}

export interface DosAuditItem {
  id: string;
  name: string;
  status: AuditStatus;
  latency_ms: number;
  metric_label: string;
  upstream_behavior: string;
  enhanced_behavior: string;
}

export interface DosAuditResponse {
  passed: number;
  total: number;
  audits: DosAuditItem[];
}

export interface BenchmarkRow {
  tier: string;
  dataset: string;
  metric: string;
  upstream_value: string;
  enhanced_value: string;
  delta: string;
  status: 'PASSED' | 'FAILED' | 'BASELINE';
}

export interface BenchmarksSummaryResponse {
  status: 'verified' | 'preview';
  generated_at: string;
  upstream_baseline: string;
  rows: BenchmarkRow[];
  ood_security: {
    secret_recall_pct: number;
    secret_total: number;
    benign_fpr_pct: number;
    benign_total: number;
  };
}
