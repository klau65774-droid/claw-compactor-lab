"""Live diagnostics for the workbench's four bounded-resource checks."""

from __future__ import annotations

from statistics import median
from time import perf_counter

from ..fusion.base import FusionContext
from ..fusion.ionizer import Ionizer
from ..fusion.quantum_lock import QuantumLock
from ..fusion.semantic_dedup import SemanticDedup
from ..rewind.store import RewindStore
from ..tokens import estimate_tokens
from ._contract import CompressCompareRequest, DosAuditItem, DosAuditResponse
from ._fixtures import scale_text
from ._text import count_tokens
from .bilingual_dedup import BilingualLshDedupStage
from .deterministic_ionizer import BoundedRewindStore, DeterministicIonizerStage
from .monotonic_pipeline import EnhancedCompactorEngine


def dedup_timings() -> tuple[float, float, float]:
    small = FusionContext(scale_text(200))
    large = FusionContext(scale_text())
    stage = BilingualLshDedupStage()

    def measure(ctx: FusionContext) -> float:
        original = count_tokens(ctx.content)
        samples: list[float] = []
        for _ in range(3):
            started = perf_counter()
            stage.apply(ctx, original_tokens=original)
            samples.append((perf_counter() - started) * 1000)
        return median(samples)

    # Both paths start with the source token count already available.
    enhanced_small = measure(small)
    enhanced_large = measure(large)
    baseline = SemanticDedup()
    estimate_tokens(large.content)
    started = perf_counter()
    baseline.apply(large)
    upstream_large = (perf_counter() - started) * 1000
    return upstream_large, enhanced_small, enhanced_large


def _scale() -> DosAuditItem:
    upstream, small, enhanced = dedup_timings()
    speedup = upstream / enhanced
    size_ratio = enhanced / small
    return DosAuditItem(
        id="semantic_dedup_scale", name="Bilingual LSH deduplication at 400 blocks",
        status="PASS" if speedup > 20 and size_ratio < 3.2 else "FAIL",
        latency_ms=round(enhanced, 3), metric_label=f"{speedup:.2f}× speedup; 200→400: {size_ratio:.2f}×",
        upstream_behavior=f"{upstream:.3f} ms for 400 blocks",
        enhanced_behavior=f"Source tokens pre-counted: {small:.3f} ms for 200 blocks; {enhanced:.3f} ms for 400 blocks",
    )


def _deep_json() -> DosAuditItem:
    inputs = [FusionContext("[" * depth + "0" + "]" * depth, content_type="json") for depth in (64, 2048)]
    started = perf_counter()
    results = [DeterministicIonizerStage().apply(ctx) for ctx in inputs]
    elapsed = (perf_counter() - started) * 1000
    valid = all(
        result.skipped and result.content == ctx.content and result.warnings
        for ctx, result in zip(inputs, results)
    )
    try:
        Ionizer().apply(inputs[-1])
    except RecursionError:
        baseline = "RecursionError at depth 2048"
    else:
        baseline = "The depth 2048 probe returned without RecursionError"
    return DosAuditItem(
        id="deep_json_nesting", name="JSON nesting preflight",
        status="PASS" if valid else "FAIL", latency_ms=round(elapsed, 3), metric_label="Maximum depth: 32",
        upstream_behavior=baseline,
        enhanced_behavior=f"Left {sum(r.skipped and bool(r.warnings) for r in results)}/2 deep inputs intact",
    )


def _rewind_limits() -> DosAuditItem:
    started = perf_counter()
    store = BoundedRewindStore()
    for i in range(501):
        store.store(str(i), "")
    byte_store = BoundedRewindStore(max_entries=8, max_bytes=40)
    first = byte_store.store("a" * 12, "A" * 4)
    second = byte_store.store("b" * 12, "B" * 4)
    assert first is not None and second is not None
    byte_store.retrieve(first)
    byte_store.store("c" * 12, "C" * 4)
    skipped_large = byte_store.store("d" * 41, "") is None
    valid = (
        store.size == 500 and store.max_bytes == 16 * 1024 * 1024 and byte_store.total_bytes == 32
        and byte_store.retrieve(second) is None and byte_store.retrieve(first) is not None and skipped_large
    )
    elapsed = (perf_counter() - started) * 1000

    baseline = RewindStore(max_entries=8)
    for char in "abc":
        baseline.store(char * 12, char.upper() * 4)
    return DosAuditItem(
        id="rewind_store_lru_cap", name="Rewind entry and byte caps",
        status="PASS" if valid else "FAIL", latency_ms=round(elapsed, 3), metric_label="500 entries / 16 MiB",
        upstream_behavior=f"Retains {baseline.size * 16} bytes with the same 3-entry workload",
        enhanced_behavior=f"{store.size} entries after 501 inserts; {byte_store.total_bytes}/40 bytes in LRU probe",
    )


def _rollback() -> DosAuditItem:
    source = "From 2026-01-15 to 2026-06-30."
    ctx = FusionContext(source, role="system")
    baseline = QuantumLock().apply(ctx)
    started = perf_counter()
    outcome = EnhancedCompactorEngine().compress(CompressCompareRequest(
        text=source, content_type="text", role="system", redact_secrets=True,
        deterministic_seed=0, mode="lossless",
    ))
    step = outcome.stages[-1]
    valid = step.rolled_back and outcome.compressed_text == source and outcome.original_tokens == outcome.compressed_tokens
    return DosAuditItem(
        id="non_expansion_rollback", name="Monotonic token rollback",
        status="PASS" if valid else "FAIL", latency_ms=round((perf_counter() - started) * 1000, 3),
        metric_label=f"{outcome.original_tokens} → {outcome.compressed_tokens} enhanced tokens",
        upstream_behavior=f"{baseline.original_tokens} → {baseline.compressed_tokens} tokens",
        enhanced_behavior=f"Expanding candidate rolled back: {step.rolled_back}",
    )


def run_dos_audit() -> DosAuditResponse:
    checks = [_scale(), _deep_json(), _rewind_limits(), _rollback()]
    return DosAuditResponse(passed=sum(check.status == "PASS" for check in checks), total=len(checks), audits=checks)
