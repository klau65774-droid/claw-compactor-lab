# ruff: noqa: UP045
"""Pydantic models for the Context Lab frontend contract (including Python 3.9)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

VerdictStatus = Literal["PASSED", "DEGRADED", "BLOATED", "VULNERABLE", "FAILED"]
CompressionMode = Literal["balanced", "lossless", "aggressive"]
MessageRole = Literal["system", "user", "assistant", "tool"]
ContentTypeHint = Literal["text", "json", "code", "log", "diff", "search"]
Engine = Literal["upstream", "enhanced"]
SecurityVerdict = Literal["SAFE", "VULNERABLE"]
AuditStatus = Literal["PASS", "FAIL"]


class HealthResponse(BaseModel):
    status: str
    upstream_version: str
    upstream_commit: str
    tiktoken_enabled: bool
    active_stages: int
    rewind_entries: int
    mode: str


class PresetItem(BaseModel):
    id: str
    name: str
    subtitle: str
    role: MessageRole
    content_type: ContentTypeHint
    mode: CompressionMode
    target_budget_tokens: int
    redact_secrets: bool
    query: str
    text: str
    description: str


class PresetsResponse(BaseModel):
    presets: list[PresetItem]


class CompressCompareRequest(BaseModel):
    preset_id: Optional[str] = Field(default=None, max_length=100)
    text: str = Field(max_length=512_000)
    role: MessageRole
    content_type: ContentTypeHint
    query: Optional[str] = Field(default=None, max_length=2048)
    target_budget_tokens: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    redact_secrets: bool
    deterministic_seed: int = Field(ge=-(2**53 - 1), le=2**53 - 1)
    mode: CompressionMode


class StageStepMetrics(BaseModel):
    name: str
    order: int
    skipped: bool
    rolled_back: bool
    original_tokens: int
    compressed_tokens: int
    delta_tokens: int
    timing_ms: float
    note: str


class RewindMarkerRef(BaseModel):
    hash_id: str
    original_count: int
    compressed_count: int
    original_tokens: int


class EngineRunOutcome(BaseModel):
    engine: Engine
    original_tokens: int
    compressed_tokens: int
    reduction_pct: float
    latency_ms: float
    prefix_hash: str
    prefix_deterministic: bool
    secret_leaks_count: int
    needle_retention_pct: float
    structure_preserved: bool
    verdict: VerdictStatus
    stages: list[StageStepMetrics]
    compressed_text: str
    rewind_markers: list[RewindMarkerRef]
    notes: list[str]


class CompressCompareResponse(BaseModel):
    preset_id: str
    mode: CompressionMode
    upstream: EngineRunOutcome
    enhanced: EngineRunOutcome
    token_delta: int
    speedup_ratio: float


class ChatTurnMessage(BaseModel):
    role: MessageRole
    content: str = Field(max_length=512_000)
    name: Optional[str] = Field(default=None, max_length=200)
    tool_call_id: Optional[str] = Field(default=None, max_length=200)


class KvCacheSimulateRequest(BaseModel):
    runs: int = Field(ge=2, le=30)
    messages: list[ChatTurnMessage] = Field(min_length=1, max_length=64)
    keep_recent: int = Field(ge=0, le=64)
    max_tokens_per_result: int = Field(ge=1, le=1_000_000)
    query: Optional[str] = Field(default=None, max_length=2048)
    redact_secrets: bool


class KvCacheTrackResult(BaseModel):
    engine: Engine
    runs: int
    unique_hashes: int
    prefix_hit_rate_pct: float
    hashes: list[str]
    tail_exception_retained: bool
    indexed_placeholders_ok: bool
    secret_leaks_count: int
    compressed_tokens: int
    original_tokens: int
    verdict: VerdictStatus
    sample_output: str


class KvCacheSimulateResponse(BaseModel):
    upstream: KvCacheTrackResult
    enhanced: KvCacheTrackResult
    hit_rate_gain_pct: float


class RewindRetrieveRequest(BaseModel):
    hash_id: str = Field(pattern=r"^[a-f0-9]{24}$")


class RewindRetrieveResponse(BaseModel):
    found: bool
    hash_id: str
    original_text: Optional[str]
    original_tokens: int
    compressed_tokens: int
    evicted_or_expired: bool


class SecurityRedactRequest(BaseModel):
    text: str = Field(max_length=128_000)
    redact_secrets: bool
    index_placeholders: bool


class SecretFinding(BaseModel):
    kind: str
    placeholder: str
    masked_preview: str
    count: int


class SecurityTrackResult(BaseModel):
    verdict: SecurityVerdict
    raw_secret_echoed: bool
    findings: list[SecretFinding]
    stabilized_text: str
    prefix_hash: str
    expansion_tokens: int


class SecurityRedactResponse(BaseModel):
    upstream: SecurityTrackResult
    enhanced: SecurityTrackResult


class DosAuditItem(BaseModel):
    id: str
    name: str
    status: AuditStatus
    latency_ms: float
    metric_label: str
    upstream_behavior: str
    enhanced_behavior: str


class DosAuditResponse(BaseModel):
    passed: int
    total: int
    audits: list[DosAuditItem]


class BenchmarkRow(BaseModel):
    tier: str
    dataset: str
    metric: str
    upstream_value: str
    enhanced_value: str
    delta: str
    status: Literal["PASSED", "FAILED", "BASELINE"]


class OodSecurity(BaseModel):
    secret_recall_pct: float
    secret_total: int
    benign_fpr_pct: float
    benign_total: int


class BenchmarksSummaryResponse(BaseModel):
    status: Literal["verified", "preview"]
    generated_at: str
    upstream_baseline: str
    rows: list[BenchmarkRow]
    ood_security: OodSecurity
