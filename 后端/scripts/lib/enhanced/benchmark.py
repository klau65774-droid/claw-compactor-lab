"""Verify the CPU pipeline against external datasets and isolated synthetic probes."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

from ..fusion.base import FusionContext
from ..fusion.quantum_lock import stabilize as upstream_stabilize
from ..fusion.semantic_dedup import SemanticDedup
from ..tokens import using_tiktoken
from ._artifacts import DOCS_DIR, write_artifact
from ._audit import run_dos_audit
from ._contract import (
    BenchmarkRow,
    BenchmarksSummaryResponse,
    ChatTurnMessage,
    CompressCompareRequest,
    ContentTypeHint,
    EngineRunOutcome,
    KvCacheSimulateRequest,
    OodSecurity,
)
from ._fixtures import chinese_json, chinese_rag, python_traceback
from ._simulation import simulate_kv_cache
from ._text import count_tokens
from .bilingual_dedup import BilingualLshDedupStage
from .monotonic_pipeline import (
    UPSTREAM_BASELINE,
    EnhancedCompactorEngine,
    run_upstream_baseline,
)
from .safe_quantum_lock import SafeQuantumLockStage

_LONG_BENCH = {
    "dureader": 200, "multifieldqa_zh": 200, "hotpotqa": 200, "qasper": 200,
    "lcc": 500, "repobench-p": 500,
}
_CODE_SUBSETS = {"lcc", "repobench-p"}


class DatasetError(ValueError):
    """A required dataset is incomplete or unreadable."""


def _records(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value: object = json.loads(line)
                if not isinstance(value, dict):
                    raise DatasetError(f"Expected JSON objects in {path.name}")
                yield value


def _swe_records(root: Path) -> Iterator[dict[str, object]]:
    directory = root / "swebench"
    jsonl = sorted(directory.glob("*.jsonl"))
    if jsonl:
        for path in jsonl:
            yield from _records(path)
        return
    path = directory / "test-00000-of-00001.parquet"
    if not path.is_file():
        raise DatasetError("SWE-bench Lite parquet or JSONL is missing")
    try:
        parquet = import_module("pyarrow.parquet")
    except ImportError as error:
        raise DatasetError("SWE-bench Lite parquet requires pyarrow or an external JSONL export") from error
    source = parquet.ParquetFile(path)
    try:
        for batch in source.iter_batches(batch_size=32, columns=["instance_id", "patch", "problem_statement"]):
            for row in batch.to_pylist():
                yield dict(row)
    finally:
        source.close()


def _string(row: dict[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise DatasetError(f"Missing or invalid field: {field}")
    return value


def _row(
    tier: str, dataset: str, metric: str, upstream: str, enhanced: str,
    delta: str = "", passed: bool | None = None,
) -> BenchmarkRow:
    return BenchmarkRow(
        tier=tier, dataset=dataset, metric=metric, upstream_value=upstream, enhanced_value=enhanced,
        delta=delta, status="BASELINE" if passed is None else "PASSED" if passed else "FAILED",
    )


def _pct(numerator: int, denominator: int) -> float:
    return 100 * numerator / denominator if denominator else 0.0


@dataclass
class _DatasetMetrics:
    original: int = 0
    upstream: int = 0
    enhanced: int = 0
    base_expansions: int = 0
    enhanced_expansions: int = 0
    base_failed: int = 0
    enhanced_failed: int = 0
    references: int = 0
    base_retained: int = 0
    enhanced_retained: int = 0

    def observe(
        self, base: EngineRunOutcome, enhanced: EngineRunOutcome, references: list[str], *,
        exact: bool = False,
    ) -> None:
        self.original += enhanced.original_tokens
        self.upstream += base.compressed_tokens
        self.enhanced += enhanced.compressed_tokens
        self.base_expansions += int(base.compressed_tokens > base.original_tokens)
        self.enhanced_expansions += int(enhanced.compressed_tokens > enhanced.original_tokens)
        self.base_failed += int(base.verdict == "FAILED")
        self.enhanced_failed += int(enhanced.verdict == "FAILED")
        self.references += bool(references)
        base_text = base.compressed_text if exact else base.compressed_text.casefold()
        enhanced_text = enhanced.compressed_text if exact else enhanced.compressed_text.casefold()
        self.base_retained += any(ref in base_text for ref in references)
        self.enhanced_retained += any(ref in enhanced_text for ref in references)

    def rows(self, tier: str, name: str, retention_metric: str, count: int) -> list[BenchmarkRow]:
        baseline = _pct(self.original - self.upstream, self.original)
        enhanced = _pct(self.original - self.enhanced, self.original)
        up_value = f"{baseline:.2f}%" if not self.base_failed else f"N/A ({self.base_failed} failed runs)"
        en_value = f"{enhanced:.2f}%" if not self.enhanced_failed else f"N/A ({self.enhanced_failed} failed runs)"
        return [
            _row(
                tier, name, f"Token reduction across {count} full rows", up_value, en_value,
                f"{enhanced - baseline:+.2f} pp" if not (self.base_failed or self.enhanced_failed) else "",
            ),
            _row(
                tier, name, "Expanding outputs", str(self.base_expansions), str(self.enhanced_expansions),
                str(self.enhanced_expansions - self.base_expansions),
                self.enhanced_expansions == 0 and self.enhanced_failed == 0,
            ),
            _row(
                tier, name, retention_metric,
                f"{self.base_retained}/{self.references}", f"{self.enhanced_retained}/{self.references}",
                f"{_pct(self.enhanced_retained - self.base_retained, self.references):+.2f} pp",
                self.enhanced_retained >= self.base_retained if self.references else None,
            ),
        ]


def _request(
    text: str, content_type: ContentTypeHint, query: str | None,
) -> CompressCompareRequest:
    budget = max(1, int(count_tokens(text) * 0.85)) if content_type == "text" else None
    return CompressCompareRequest(
        text=text, role="user", content_type=content_type, query=query, target_budget_tokens=budget,
        redact_secrets=True, deterministic_seed=0, mode="balanced",
    )


def _longbench(root: Path, engine: EnhancedCompactorEngine) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    for name, expected in _LONG_BENCH.items():
        source = root / "longbench" / "subsets" / f"{name}.jsonl"
        metrics = _DatasetMetrics()
        seen: set[str] = set()
        is_code = name in _CODE_SUBSETS
        for record in _records(source):
            record_id = _string(record, "_id")
            if record_id in seen:
                raise DatasetError(f"Duplicate record in LongBench {name}")
            seen.add(record_id)
            context = _string(record, "context")
            # Gold code continuations are held out. Check the last source line.
            if is_code:
                query = None
                boundary = next((line for line in reversed(context.splitlines()) if line.strip()), "")
                refs = [boundary] if boundary else []
            else:
                query = _string(record, "input")
                answers = record.get("answers")
                if not isinstance(answers, list):
                    raise DatasetError(f"Missing answer list in LongBench {name}")
                folded = context.casefold()
                refs = [
                    answer.strip().casefold() for answer in answers if isinstance(answer, str)
                    and answer.strip() and answer.strip().casefold() in folded
                ]
            req = _request(context, "code" if is_code else "text", query)
            metrics.observe(run_upstream_baseline(req, check_repeat=False), engine.compress(req), refs, exact=is_code)
        if len(seen) != expected:
            raise DatasetError(f"LongBench {name} has {len(seen)} rows; expected {expected}")
        label = "Source boundary retained" if is_code else "Extractive answers retained among source-present rows"
        rows.extend(metrics.rows("1", f"THUDM/LongBench/{name}", label, expected))
        print(f"LongBench {name}: {len(seen)} rows verified", flush=True)
    return rows


def _swebench(root: Path, engine: EnhancedCompactorEngine) -> list[BenchmarkRow]:
    metrics = _DatasetMetrics()
    seen: set[str] = set()
    for record in _swe_records(root):
        record_id = _string(record, "instance_id")
        if record_id in seen:
            raise DatasetError("Duplicate SWE-bench Lite instance")
        seen.add(record_id)
        patch = _string(record, "patch").strip("\r\n")
        text = _string(record, "problem_statement") + "\n\n" + patch
        req = _request(text, "text", None)
        # Diff data is compressed as text. No patches or tests are run.
        metrics.observe(
            run_upstream_baseline(req, check_repeat=False), engine.compress(req),
            [patch] if patch else [], exact=True,
        )
    if len(seen) != 300:
        raise DatasetError(f"SWE-bench Lite has {len(seen)} rows; expected 300")
    print("SWE-bench Lite: 300 rows verified", flush=True)
    return metrics.rows("2", "princeton-nlp/SWE-bench_Lite", "Complete patch retained", 300)


def _ood_security() -> tuple[OodSecurity, list[BenchmarkRow]]:
    # Synthetic format/decoy checks, separate from the presets and unit tests.
    synthetic = [
        "sk-proj-SyntheticLabQp9uRvTz48J0",
        "sk-svcacct-ContextLabOnlyS5Y6Z7X8",
        "sk-TestFixtureZYXwvuTSR098765",
        "rk-ScopedFixture05_B3j9dNA",
        "LTAI9DryRunOnly27Nc5Qh",
        "LTAIProjectionVo8K3APq795S2k",
        "AKIA0000LABX2468DEMO",
        "ASIA9876TRAINING3456",
        "ghp_AabbCCddEEff0011223344556677",
        "gho_LabSandboxA33BBB222XYZ9123",
        "github_pat_15Demo_UQ0O9yFReAr31ZC",
        "eyJhbGciOiJub25lIn0.eyJpc3MiOiJsYWIifQ.synthetic-signature",
        "eyJraWQiOiJkZW1vIn0.eyJleHAiOjE3MDAwMDAwMDB9.invalid_signature",
        "18700004631",
        "+86 19500003426",
    ]
    wrappers = ("凭据{}请勿引用", "Authorization: Bearer {}", 'config = {{"key":"{}"}}', "[{}]")
    decoys = [
        "request 7b9a7331-8c20-4514-8af3-13400002000f",
        "sha256 18700004631d54bfa7b899133e51f6dca97a8a5ef53d660ad0e3b561bc500599",
        "SHA-256: b922a9cd60a6536a2812ec7118cfb87051a9219248c0a19a726ed07bb35c2ef9",
        "release 2026-03-24",
        "availability window 2026-07-01T13:18:06+08:00",
        "订单号110105194912310029已登记",
        "国内订单号187000046310000000",
        "Bearer request routing is enabled for the public demo",
        "Bearer seed rotation instructions are in the readme",
        "sk-dev-label",
        "LTAI region hint",
        "AKIAINVALID8",
        "ghp_prefix is documentation",
        "eyJ_not_three_segments.payloadonly",
        "clock resolved to 19:59:00 and timestamp 1765012345000",
    ]
    hits = baseline_hits = fp = 0
    lock = SafeQuantumLockStage()
    for index, raw in enumerate(synthetic):
        sample = wrappers[index % len(wrappers)].format(raw)
        result = lock.stabilize(sample)
        hits += int(bool(result.findings) and raw not in result.text)
        baseline_hits += int(raw not in upstream_stabilize(sample))
    for sample in decoys:
        fp += int(bool(lock.stabilize(sample).findings))
    security = OodSecurity(
        secret_recall_pct=_pct(hits, len(synthetic)), secret_total=len(synthetic),
        benign_fpr_pct=_pct(fp, len(decoys)), benign_total=len(decoys),
    )
    rows = [
        _row("3", "Synthetic OOD credentials", "Detected and removed", f"{baseline_hits}/{len(synthetic)}",
             f"{hits}/{len(synthetic)}", f"{hits - baseline_hits:+d}", hits == len(synthetic)),
        _row("3", "Synthetic OOD decoys", "Secret false positives", "N/A (no secret classifier)", f"{fp}/{len(decoys)}",
             passed=fp == 0),
    ]
    return security, rows


def _operational(engine: EnhancedCompactorEngine) -> list[BenchmarkRow]:
    system = " ".join(["Start 2026-01-15, finish 2026-06-30."] * 18)
    messages = [
        ChatTurnMessage(role="system", content=system),
        ChatTurnMessage(role="tool", content=chinese_json(60), name="db_tool"),
        ChatTurnMessage(role="tool", content=python_traceback(800), name="python_tool"),
    ]
    request = KvCacheSimulateRequest(
        runs=10, messages=messages, keep_recent=2, max_tokens_per_result=6400,
        query="ZeroDivisionError compute_margin 严重异常", redact_secrets=True,
    )
    report = simulate_kv_cache(request, engine)
    up, enhanced = report.upstream, report.enhanced
    rows = [
        _row("3", "Identical transcript × 10", "Unique prefix hashes", str(up.unique_hashes),
             str(enhanced.unique_hashes), str(enhanced.unique_hashes - up.unique_hashes), enhanced.unique_hashes == 1),
        _row("3", "Identical transcript × 10", "Consecutive prefix hit rate", f"{up.prefix_hit_rate_pct:.2f}%",
             f"{enhanced.prefix_hit_rate_pct:.2f}%", f"{report.hit_rate_gain_pct:+.2f} pp", enhanced.prefix_hit_rate_pct == 100),
        _row("3", "800-frame Python traceback", "Root cause retained across runs", str(up.tail_exception_retained),
             str(enhanced.tail_exception_retained), passed=enhanced.tail_exception_retained),
        _row("3", "Two release dates", "Indexed placeholders", str(up.indexed_placeholders_ok),
             str(enhanced.indexed_placeholders_ok), passed=enhanced.indexed_placeholders_ok),
    ]
    changed = system.replace("2026-01-15", "2026-02-13").replace("2026-06-30", "2026-07-21")
    originals = [
        CompressCompareRequest(text=text, role="system", content_type="text",
                               redact_secrets=True, deterministic_seed=0, mode="lossless")
        for text in (system, changed)
    ]
    matching = engine.compress(originals[0]).prefix_hash == engine.compress(originals[1]).prefix_hash
    base_matching = run_upstream_baseline(originals[0]).prefix_hash == run_upstream_baseline(originals[1]).prefix_hash
    rows.append(_row("3", "Changed release dates", "Stable prefix hash matches", str(base_matching), str(matching), passed=matching))

    context = FusionContext(chinese_rag())
    base_rag = SemanticDedup().apply(context)
    enhanced_rag = BilingualLshDedupStage().apply(context)
    base_pct = _pct(base_rag.original_tokens - base_rag.compressed_tokens, base_rag.original_tokens)
    enhanced_pct = _pct(enhanced_rag.original_tokens - enhanced_rag.compressed_tokens, enhanced_rag.original_tokens)
    rows.append(_row("3", "12 Chinese RAG paragraphs", "Token reduction", f"{base_pct:.2f}%", f"{enhanced_pct:.2f}%",
                     f"{enhanced_pct - base_pct:+.2f} pp", enhanced_pct >= 80))

    for audit in run_dos_audit().audits:
        passed = audit.status == "PASS"
        if audit.id == "semantic_dedup_scale":
            passed = passed and audit.latency_ms < 50
        rows.append(_row("3", audit.name, audit.metric_label, audit.upstream_behavior, audit.enhanced_behavior,
                         passed=passed))
    return rows


def verify(root: Path) -> BenchmarksSummaryResponse:
    for name in _LONG_BENCH:
        if not (root / "longbench" / "subsets" / f"{name}.jsonl").is_file():
            raise DatasetError(f"LongBench {name} is missing")
    engine = EnhancedCompactorEngine()
    # Run isolated timing before caches accumulate dataset text.
    operational = _operational(engine)
    security, security_rows = _ood_security()
    print(f"Operational and OOD probes complete ({'cl100k_base' if using_tiktoken() else 'CJK heuristic'})", flush=True)
    rows = _longbench(root, engine) + _swebench(root, engine) + operational + security_rows
    return BenchmarksSummaryResponse(
        status="verified", generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        upstream_baseline=UPSTREAM_BASELINE, rows=rows, ood_security=security,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, help="External dataset directory (or set CLAW_BENCHMARK_DATASET_ROOT)")
    parser.add_argument("--output", type=Path, default=DOCS_DIR / "benchmarks_report.json")
    args = parser.parse_args()
    configured = os.environ.get("CLAW_BENCHMARK_DATASET_ROOT")
    root = args.dataset_root or (Path(configured) if configured else None)
    if root is None:
        parser.error("set CLAW_BENCHMARK_DATASET_ROOT or pass --dataset-root")
    try:
        report = verify(root)
    except DatasetError as error:
        parser.error(str(error))
    write_artifact(args.output, report.model_dump_json(indent=2))
    failed = sum(row.status == "FAILED" for row in report.rows)
    print(f"Wrote {args.output.name}: {report.status}, {len(report.rows)} metrics, {failed} failed criteria")


if __name__ == "__main__":
    main()
