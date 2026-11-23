"""Run enhanced stages behind a token gate and measure the upstream baseline."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from ..fusion.base import FusionContext, FusionResult
from ..fusion.engine import FusionEngine
from ..fusion.quantum_lock import APPENDIX_START
from ..fusion.tool_result_budget import budget_tool_results
from ..rewind.marker import extract_markers
from ._contract import (
    CompressCompareRequest,
    CompressCompareResponse,
    Engine,
    EngineRunOutcome,
    RewindMarkerRef,
    StageStepMetrics,
    VerdictStatus,
)
from ._text import count_tokens, digest, needle_retention, structure_preserved
from .bilingual_dedup import BilingualLshDedupStage
from .deterministic_ionizer import (
    BoundedRewindStore,
    DeterministicIonizerStage,
    within_json_depth,
)
from .safe_quantum_lock import (
    SafeQuantumLockStage,
    count_secret_occurrences,
    redact_inline,
)
from .smart_tool_budget import compact_tool_result, traceback_tail
from .structure_nexus import StructureNexusStage

UPSTREAM_VERSION = "7.1.0"
UPSTREAM_COMMIT = "c1b936d40b1145c7a257bd6e34a17994f467495f"
UPSTREAM_BASELINE = f"open-compress/claw-compactor v{UPSTREAM_VERSION} ({UPSTREAM_COMMIT[:12]}, MIT)"
ACTIVE_STAGES = 6


def compute_verdict(
    text: str, original: int, compressed: int, leaks: int, deterministic: bool,
    preserved: bool, retained: float,
) -> VerdictStatus:
    if not text.strip():
        return "FAILED"
    if leaks:
        return "VULNERABLE"
    if compressed > original:
        return "BLOATED"
    if not deterministic or not preserved or retained < 70:
        return "DEGRADED"
    return "PASSED"


def redact_payload(text: str, content_type: str) -> str:
    masked = redact_inline(text)
    # JSON can spell a credential with escaped ASCII characters. Check the
    # decoded representation before Ionizer can surface it or store it.
    if content_type == "json" and "\\" in masked and within_json_depth(masked):
        try:
            compact = json.dumps(
                json.loads(masked), ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
            compact.encode("utf-8")
        except (ValueError, RecursionError):
            return masked
        sanitized = redact_inline(compact)
        if sanitized != compact:
            return sanitized
    return masked


def _is_json(text: str) -> bool:
    if not within_json_depth(text):
        return False
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return False
    return True


def _kept_structure(
    original: str, text: str, req: CompressCompareRequest, generated_marker: bool,
) -> bool:
    if original == text:
        return True
    if req.content_type == "json":
        payload = text.rsplit("\n", 1)[0] if generated_marker else text
        return _is_json(payload) or not _is_json(original)
    if req.content_type == "log":
        tail = traceback_tail(original)
        return not tail or tail in text
    return structure_preserved(original, text, req.content_type)


@dataclass(frozen=True)
class CompressionRun:
    outcome: EngineRunOutcome
    stable_prefix: str
    dynamic_appendix: str = ""
    indexed_placeholders: bool = False


def _metrics(
    name: str, order: int, before: int, after: int, timing: float, *,
    skipped: bool = False, rolled_back: bool = False, note: str = "",
) -> StageStepMetrics:
    return StageStepMetrics(
        name=name, order=order, skipped=skipped, rolled_back=rolled_back,
        original_tokens=before, compressed_tokens=after, delta_tokens=before - after,
        timing_ms=round(timing, 3), note=note,
    )


def _outcome(
    req: CompressCompareRequest, *, engine: Engine, raw_output: str, prefix: str,
    input_for_structure: str, original_tokens: int, started: float,
    deterministic: bool, stages: list[StageStepMetrics], markers: list[RewindMarkerRef],
    notes: list[str],
) -> EngineRunOutcome:
    compressed = count_tokens(raw_output)
    display = raw_output
    leaks = count_secret_occurrences(raw_output)
    if engine == "upstream" and req.redact_secrets:
        display = redact_payload(raw_output, req.content_type)
        if display != raw_output:
            notes.append("Baseline metrics were measured before masking the displayed text.")
    kept = _kept_structure(input_for_structure, raw_output, req, bool(markers))
    retained = needle_retention(req.text, raw_output, req.query)
    reduction = 100 * (original_tokens - compressed) / original_tokens if original_tokens else 0.0
    return EngineRunOutcome(
        engine=engine, original_tokens=original_tokens, compressed_tokens=compressed,
        reduction_pct=round(reduction, 2), latency_ms=round((perf_counter() - started) * 1000, 3),
        prefix_hash=digest(prefix), prefix_deterministic=deterministic,
        secret_leaks_count=leaks, needle_retention_pct=retained, structure_preserved=kept,
        verdict=compute_verdict(raw_output, original_tokens, compressed, leaks, deterministic, kept, retained),
        stages=stages, compressed_text=display, rewind_markers=markers, notes=notes,
    )


class EnhancedCompactorEngine:
    """Run a pure CPU pipeline with a shared, bounded rewind store."""

    def __init__(self, rewind_store: BoundedRewindStore | None = None) -> None:
        self.rewind_store = rewind_store if rewind_store is not None else BoundedRewindStore()

    def iter_compress(
        self, req: CompressCompareRequest,
    ) -> Iterator[StageStepMetrics | CompressionRun]:
        started = perf_counter()
        original = count_tokens(req.text)
        begin_stage = perf_counter()
        safe = redact_payload(req.text, req.content_type) if req.redact_secrets else req.text
        current = count_tokens(safe) if safe != req.text else original
        stages = [_metrics(
            "inline_redaction", 1, original, current, (perf_counter() - begin_stage) * 1000,
            skipped=safe == req.text, note="Credential removal takes precedence over the token budget."
            if safe != req.text else "",
        )]
        yield stages[-1]

        lossless = req.mode == "lossless"
        aggressive = req.mode == "aggressive"
        store = self.rewind_store
        persist = req.redact_secrets or redact_payload(safe, req.content_type) == safe
        usable_store = store if persist else None
        ctx = FusionContext(
            safe, content_type=req.content_type, role=req.role,
            token_budget=req.target_budget_tokens, query=req.query,
        )
        stages_to_run = (
            (DeterministicIonizerStage(
                usable_store, deterministic_seed=req.deterministic_seed,
                sample_size=None if lossless else 12 if aggressive else 20,
            ), True),
            (BilingualLshDedupStage(0.77 if aggressive else 0.80), not lossless and req.role != "system"),
            (StructureNexusStage(aggressive=aggressive), not lossless and req.role not in {"system", "tool"}),
        )
        text = safe
        markers: list[RewindMarkerRef] = []
        for stage, enabled in stages_to_run:
            begin_stage = perf_counter()
            stage_context = replace(ctx, content=text)
            result = (
                stage.apply(stage_context, original_tokens=current)
                if enabled and stage.should_apply(stage_context) else None
            )
            text, step = self._gate(stage.name, stage.order, text, current, result, begin_stage, markers)
            current = step.compressed_tokens
            stages.append(step)
            yield step

        begin_stage = perf_counter()
        tool = None
        if req.role == "tool" and req.content_type in {"text", "search", "log"} and not lossless:
            tool = compact_tool_result(
                text, max_tokens=req.target_budget_tokens or 8000, query=req.query,
                rewind_store=usable_store,
            )
        text, step = self._gate("smart_tool_budget", 40, text, current, tool, begin_stage, markers)
        current = step.compressed_tokens
        stages.append(step)
        yield step

        begin_stage = perf_counter()
        lock = SafeQuantumLockStage(redact_secrets=req.redact_secrets)
        # Use the original system text so any accepted appendix contains only
        # masked secret previews. The rollback candidate is already redacted.
        locked = lock.stabilize(req.text) if lock.should_apply(FusionContext(
            req.text, content_type=req.content_type, role=req.role,
        )) else None
        lock_result = (
            FusionResult(locked.text, current, count_tokens(locked.text))
            if locked is not None else None
        )
        text, step = self._gate(lock.name, lock.order, text, current, lock_result, begin_stage, markers)
        stages.append(step)
        yield step
        accepted = locked if locked and not step.rolled_back else None
        prefix = accepted.prefix if accepted else text

        notes: list[str] = []
        if req.target_budget_tokens is not None and step.compressed_tokens > req.target_budget_tokens:
            notes.append("Protected content and rewind metadata may exceed the requested target.")
        outcome = _outcome(
            req, engine="enhanced", raw_output=text, prefix=prefix, input_for_structure=safe,
            original_tokens=original, started=started, deterministic=True, stages=stages,
            markers=markers, notes=notes,
        )
        yield CompressionRun(
            outcome, prefix, accepted.appendix if accepted else "", accepted.indexed if accepted else False,
        )

    @staticmethod
    def _gate(
        name: str, order: int, text: str, before: int, result: FusionResult | None,
        started: float, markers: list[RewindMarkerRef],
    ) -> tuple[str, StageStepMetrics]:
        skipped = result is None or result.skipped or result.content == text
        proposed = before if skipped or result is None else result.compressed_tokens
        rollback = proposed > before
        note = "Candidate exceeded the incoming token count." if rollback else ""
        if result and result.warnings:
            note = " ".join([note, *result.warnings]).strip()
        if result is not None and not skipped and not rollback:
            text = result.content
            if result.markers and result.markers[-1] in text[-120:]:
                markers.extend(
                    RewindMarkerRef(
                        hash_id=marker.hash_id, original_count=marker.original_count,
                        compressed_count=marker.compressed_count, original_tokens=before,
                    )
                    for marker in extract_markers(text.rsplit("\n", 1)[-1])
                    if marker.hash_id in result.markers
                )
        return text, _metrics(
            name, order, before, before if rollback else proposed, (perf_counter() - started) * 1000,
            skipped=skipped, rolled_back=rollback, note=note,
        )

    def run(self, req: CompressCompareRequest) -> CompressionRun:
        for item in self.iter_compress(req):
            if isinstance(item, CompressionRun):
                return item
        raise RuntimeError("compression ended without a result")

    def compress(self, req: CompressCompareRequest) -> EngineRunOutcome:
        return self.run(req).outcome


def upstream_prefix(result: str) -> str:
    return result.partition(f"\n---\n{APPENDIX_START}")[0]


def run_upstream_baseline(
    req: CompressCompareRequest, *, check_repeat: bool = True, budget_tool: bool = True,
) -> EngineRunOutcome:
    """Measure the shipped FusionEngine with its original stage implementations."""
    started = perf_counter()
    text = req.text
    original = count_tokens(text)
    stages: list[StageStepMetrics] = []
    notes: list[str] = []
    engine = FusionEngine(enable_rewind=False)
    opts: dict[str, Any] = {
        "content_type": req.content_type, "role": req.role,
        "token_budget": req.target_budget_tokens, "query": req.query,
    }
    try:
        if budget_tool and req.role == "tool" and req.content_type in {"text", "search", "log"}:
            begin_stage = perf_counter()
            budgeted, _ = budget_tool_results(
                [{"role": "tool", "content": text}], keep_recent=1,
                max_tokens_per_result=req.target_budget_tokens or 8000,
            )
            text = str(budgeted[0]["content"])
            stages.append(_metrics(
                "tool_result_budget", 0, original, count_tokens(text), (perf_counter() - begin_stage) * 1000,
                skipped=text == req.text,
            ))
        result = engine.compress(text, **opts)
        raw = str(result["compressed"])
        stage_orders = {stage.name: stage.order for stage in engine.pipeline.transforms}
        before = count_tokens(text)
        for stage in result["stats"]["per_stage"]:
            skipped = bool(stage["skipped"])
            after = before if skipped else int(stage["compressed_tokens"])
            name = str(stage["name"])
            stages.append(_metrics(
                name, stage_orders[name], before, after, float(stage["timing_ms"]), skipped=skipped,
            ))
            before = after
        measured_at = perf_counter()
        repeat = check_repeat and any(s.name == "ionizer" and not s.skipped for s in stages)
        deterministic = not repeat or upstream_prefix(str(engine.compress(text, **opts)["compressed"])) == upstream_prefix(raw)
        started += perf_counter() - measured_at
    except (ValueError, RecursionError):
        raw = ""
        deterministic = False
        notes.append("The upstream engine could not process the input.")
    return _outcome(
        req, engine="upstream", raw_output=raw, prefix=upstream_prefix(raw),
        input_for_structure=req.text, original_tokens=original, started=started,
        deterministic=deterministic, stages=stages, markers=[], notes=notes,
    )


def comparison_response(
    req: CompressCompareRequest, upstream: EngineRunOutcome, enhanced: EngineRunOutcome,
) -> CompressCompareResponse:
    preset = req.preset_id or "custom"
    return CompressCompareResponse(
        preset_id=redact_inline(preset) if req.redact_secrets else preset,
        mode=req.mode, upstream=upstream, enhanced=enhanced,
        token_delta=upstream.compressed_tokens - enhanced.compressed_tokens,
        speedup_ratio=round(upstream.latency_ms / enhanced.latency_ms, 2) if enhanced.latency_ms else 0.0,
    )
