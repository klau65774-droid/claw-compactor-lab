"""Run repeated cache-prefix trials using actual compressed transcripts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..fusion.base import FusionContext
from ..fusion.ionizer import Ionizer
from ..fusion.quantum_lock import QuantumLock
from ..fusion.tool_result_budget import EXEMPT_TOOLS, budget_tool_results
from ._contract import (
    ChatTurnMessage,
    CompressCompareRequest,
    ContentTypeHint,
    Engine,
    KvCacheSimulateRequest,
    KvCacheSimulateResponse,
    KvCacheTrackResult,
)
from ._text import count_tokens, digest, needle_retention
from .monotonic_pipeline import (
    EnhancedCompactorEngine,
    compute_verdict,
    redact_payload,
    upstream_prefix,
)
from .safe_quantum_lock import (
    SafeQuantumLockStage,
    count_secret_occurrences,
    redact_inline,
)
from .smart_tool_budget import traceback_tail


@dataclass(frozen=True)
class _Trial:
    prefix_hash: str
    count: int
    leaks: int
    tail_kept: bool
    indexed: bool
    sample: str


def _kind(message: ChatTurnMessage) -> ContentTypeHint:
    start = message.content.lstrip()
    if message.role == "tool" and start.startswith(("[", "{")):
        return "json"
    if message.role == "tool" and "Traceback" in start:
        return "log"
    return "text"


def _track(
    req: KvCacheSimulateRequest, engine: Engine, compactor: EnhancedCompactorEngine,
) -> KvCacheTrackResult:
    messages = [msg.model_dump(exclude_none=True) for msg in req.messages]
    kinds = [_kind(message) for message in req.messages]
    original_count = sum(count_tokens(m.content) for m in req.messages)
    original = "\n\n".join(msg.content for msg in req.messages)
    tool_indices = [i for i, msg in enumerate(req.messages) if msg.role == "tool"]
    old_tools = set(tool_indices[:max(0, len(tool_indices) - req.keep_recent)])
    lock = QuantumLock()
    safe_lock = SafeQuantumLockStage()
    ionizer = Ionizer()

    def run_trial() -> _Trial:
        upstream_messages = messages
        if engine == "upstream":
            try:
                upstream_messages, _ = budget_tool_results(
                    messages, keep_recent=req.keep_recent, max_tokens_per_result=req.max_tokens_per_result,
                )
            except (ValueError, RecursionError):
                return _Trial(digest(""), 0, 0, False, False, "")
        parts: list[dict[str, Any]] = []
        suffixes: list[str] = []
        display: list[str] = []
        count = leaks = 0
        tail_kept = indexed = True
        for i, (msg, kind) in enumerate(zip(req.messages, kinds)):
            source = msg.content
            if engine == "enhanced" and req.redact_secrets:
                source = redact_payload(source, kind)
            needs_index = safe_lock.should_apply(FusionContext(msg.content, content_type=kind, role=msg.role))
            if engine == "upstream":
                text = str(upstream_messages[i]["content"])
                ctx = FusionContext(text, content_type=kind, role=msg.role)
                try:
                    if msg.role == "system" and lock.should_apply(ctx):
                        text = lock.apply(ctx).content
                    elif kind == "json" and text == source:
                        text = ionizer.apply(ctx).content
                except (ValueError, RecursionError):
                    text = ""
                prefix = upstream_prefix(text) if msg.role == "system" else text
                suffix = text[len(prefix):]
                indexed = indexed and not needs_index
            else:
                budget = None
                name = (msg.name or msg.tool_call_id or "").lower()
                exempt = any(item in name for item in EXEMPT_TOOLS)
                if msg.role == "tool" and not exempt:
                    budget = min(160, req.max_tokens_per_result) if i in old_tools else req.max_tokens_per_result
                report = compactor.run(CompressCompareRequest(
                    text=msg.content, role=msg.role, content_type=kind, query=req.query,
                    target_budget_tokens=budget, redact_secrets=req.redact_secrets,
                    deterministic_seed=0, mode="lossless" if exempt else "balanced",
                ))
                text = report.outcome.compressed_text
                prefix = report.stable_prefix
                suffix = report.dynamic_appendix
                indexed = indexed and (not needs_index or report.indexed_placeholders)
            metadata = {k: v for k, v in messages[i].items() if k != "content"}
            if req.redact_secrets and engine == "enhanced":
                metadata = {k: redact_inline(v) if isinstance(v, str) else v for k, v in metadata.items()}
            parts.append({**metadata, "content": prefix})
            preview = redact_payload(prefix, kind) if req.redact_secrets and engine == "upstream" else prefix
            display.append(f"[{msg.role}]\n{preview}")
            if suffix:
                preview = redact_inline(suffix) if req.redact_secrets and engine == "upstream" else suffix
                suffixes.append(f"[{msg.role} dynamic values]{preview}")
            tail = traceback_tail(source) if msg.role == "tool" and kind == "log" else ""
            tail_kept = tail_kept and (not tail or tail in text)
            leaks += count_secret_occurrences(text)
            count += count_tokens(text)
        # Canonical JSON preserves order, boundaries and message roles.
        # Dynamic ledgers are placed after the serialized stable messages.
        wire = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return _Trial(digest(wire), count, leaks, tail_kept, indexed, "\n\n".join(display + suffixes))

    trials = [run_trial() for _ in range(req.runs)]
    hashes = [trial.prefix_hash for trial in trials]
    hits = sum(previous == current for previous, current in zip(hashes, hashes[1:]))
    hit_pct = round(100 * hits / (req.runs - 1), 2)
    unique = len(set(hashes))
    sample = trials[0]
    retained_tail = all(trial.tail_kept for trial in trials)
    indexed = all(trial.indexed for trial in trials)
    leaks = max(trial.leaks for trial in trials)
    retained = needle_retention(original, sample.sample, req.query)
    verdict = compute_verdict(
        sample.sample if sample.count else "", original_count, sample.count, leaks,
        unique == 1, retained_tail and indexed, retained,
    )
    return KvCacheTrackResult(
        engine=engine, runs=req.runs, unique_hashes=unique, prefix_hit_rate_pct=hit_pct, hashes=hashes,
        tail_exception_retained=retained_tail, indexed_placeholders_ok=indexed, secret_leaks_count=leaks,
        compressed_tokens=sample.count, original_tokens=original_count, verdict=verdict,
        sample_output=sample.sample,
    )


def simulate_kv_cache(
    req: KvCacheSimulateRequest, compactor: EnhancedCompactorEngine,
) -> KvCacheSimulateResponse:
    upstream = _track(req, "upstream", compactor)
    enhanced = _track(req, "enhanced", compactor)
    return KvCacheSimulateResponse(
        upstream=upstream, enhanced=enhanced,
        hit_rate_gain_pct=round(enhanced.prefix_hit_rate_pct - upstream.prefix_hit_rate_pct, 2),
    )
