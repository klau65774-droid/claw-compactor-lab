"""FastAPI entry points for the ClawCompactor Context Lab."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..fusion.quantum_lock import stabilize as upstream_stabilize
from ..tokens import using_tiktoken
from ._artifacts import DOCS_DIR
from ._audit import run_dos_audit
from ._contract import (
    BenchmarksSummaryResponse,
    CompressCompareRequest,
    CompressCompareResponse,
    DosAuditResponse,
    HealthResponse,
    KvCacheSimulateRequest,
    KvCacheSimulateResponse,
    OodSecurity,
    PresetsResponse,
    RewindRetrieveRequest,
    RewindRetrieveResponse,
    SecretFinding,
    SecurityRedactRequest,
    SecurityRedactResponse,
    SecurityTrackResult,
)
from ._fixtures import presets
from ._simulation import simulate_kv_cache
from ._text import count_tokens, digest
from .monotonic_pipeline import (
    ACTIVE_STAGES,
    UPSTREAM_BASELINE,
    UPSTREAM_COMMIT,
    UPSTREAM_VERSION,
    CompressionRun,
    EnhancedCompactorEngine,
    comparison_response,
    run_upstream_baseline,
    upstream_prefix,
)
from .safe_quantum_lock import (
    SafeQuantumLockStage,
    count_secret_occurrences,
    redact_inline,
)

_MAX_BODY_BYTES = 1024 * 1024
BENCHMARK_REPORT = DOCS_DIR / "benchmarks_report.json"
ENGINE = EnhancedCompactorEngine()


class _LimitBody:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > _MAX_BODY_BYTES:
                await JSONResponse({"detail": "Request body exceeds 1 MiB."}, status_code=413)(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body"):
                break
        delivered = False

        async def receive_body() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                buffered = bytes(body)
                body.clear()
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, receive_body, send)


app = FastAPI(title="ClawCompactor Context Lab API", version="0.1.0")
app.add_middleware(_LimitBody)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
    return JSONResponse({"detail": details}, status_code=422)


@app.get("/api/v1/health", summary="Engine health, upstream v7.1.0 baseline metadata, tiktoken status, and active stages")
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", upstream_version=UPSTREAM_VERSION, upstream_commit=UPSTREAM_COMMIT,
        tiktoken_enabled=using_tiktoken(), active_stages=ACTIVE_STAGES,
        rewind_entries=ENGINE.rewind_store.size, mode="live",
    )


@app.get("/api/v1/presets", summary="List canonical bilingual context engineering and failure-reproduction presets")
def list_presets() -> PresetsResponse:
    return PresetsResponse(presets=presets())


@app.post("/api/v1/compress/compare", summary="Compare unmodified upstream claw-compactor v7.1.0 vs enhanced monotonic bilingual engine")
def compare(req: CompressCompareRequest) -> CompressCompareResponse:
    return comparison_response(req, run_upstream_baseline(req), ENGINE.compress(req))


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@app.post(
    "/api/v1/compress/stream",
    summary="Stream real-time stage, milestone, and complete SSE compression events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}},
                     "x-sse-events": ["stage", "milestone", "complete"]}},
)
def stream(req: CompressCompareRequest) -> StreamingResponse:
    def events() -> Iterator[str]:
        yield _sse("stage", {"progress_pct": 0, "stage": "upstream_baseline"})
        upstream = run_upstream_baseline(req)
        yield _sse("milestone", {"progress_pct": 15, "stage": "upstream_baseline_complete"})
        stage_number = 0
        for item in ENGINE.iter_compress(req):
            if isinstance(item, CompressionRun):
                yield _sse("complete", comparison_response(req, upstream, item.outcome).model_dump())
            else:
                stage_number += 1
                yield _sse("stage", {
                    "progress_pct": 15 + round(85 * stage_number / ACTIVE_STAGES),
                    "stage": item.name, "metrics": item.model_dump(),
                })

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.post("/api/v1/kv-cache/simulate", summary="Evaluate repeated-run KV-cache prefix determinism, indexed placeholders, and traceback tail retention")
def kv_cache(req: KvCacheSimulateRequest) -> KvCacheSimulateResponse:
    if sum(len(msg.content) for msg in req.messages) * req.runs > 3_000_000:
        raise HTTPException(status_code=413, detail="Transcript and run count exceed the simulation workload limit.")
    return simulate_kv_cache(req, ENGINE)


@app.post("/api/v1/rewind/retrieve", summary="Retrieve lossless original payload from the bounded LRU RewindStore by 24-char hash ID")
def rewind(req: RewindRetrieveRequest) -> RewindRetrieveResponse:
    entry = ENGINE.rewind_store.get(req.hash_id)
    return RewindRetrieveResponse(
        found=entry is not None, hash_id=req.hash_id,
        original_text=entry.original if entry is not None else None,
        original_tokens=entry.original_tokens if entry is not None else 0,
        compressed_tokens=entry.compressed_tokens if entry is not None else 0,
        evicted_or_expired=entry is None,
    )


@app.post("/api/v1/security/redact", summary="Detect raw secret echo in upstream QuantumLock and enforce safe redaction with indexed placeholders")
def security(req: SecurityRedactRequest) -> SecurityRedactResponse:
    locked = SafeQuantumLockStage(
        redact_secrets=req.redact_secrets, index_placeholders=req.index_placeholders,
    ).stabilize(req.text)
    findings = [
        SecretFinding(kind=f.kind, placeholder=f.placeholder, masked_preview=f.masked_preview, count=f.count)
        for f in locked.findings
    ]
    upstream = upstream_stabilize(req.text)
    original = count_tokens(req.text)

    def track(raw: str, prefix: str, display: str) -> SecurityTrackResult:
        echoed = count_secret_occurrences(raw) > 0
        return SecurityTrackResult(
            verdict="VULNERABLE" if echoed else "SAFE", raw_secret_echoed=echoed,
            findings=findings, stabilized_text=display, prefix_hash=digest(prefix),
            expansion_tokens=count_tokens(raw) - original,
        )

    return SecurityRedactResponse(
        upstream=track(upstream, upstream_prefix(upstream), redact_inline(upstream) if req.redact_secrets else upstream),
        enhanced=track(locked.text, locked.prefix, locked.text),
    )


@app.post("/api/v1/dos/audit", summary="Execute 4 bounded-resource, O(N) dedup scaling, and non-expansion rollback security audits")
def dos_audit() -> DosAuditResponse:
    return run_dos_audit()


@app.get("/api/v1/benchmarks/summary", summary="Return 3-tier sealed external benchmark metrics (LongBench EN+ZH, SWE-bench_Lite, OOD security)")
def benchmarks_summary() -> BenchmarksSummaryResponse:
    try:
        return BenchmarksSummaryResponse.model_validate_json(BENCHMARK_REPORT.read_bytes())
    except FileNotFoundError:
        return BenchmarksSummaryResponse(
            status="preview", generated_at="", upstream_baseline=UPSTREAM_BASELINE, rows=[],
            ood_security=OodSecurity(secret_recall_pct=0, secret_total=0, benign_fpr_pct=0, benign_total=0),
        )
    except (OSError, ValidationError) as error:
        raise HTTPException(status_code=503, detail="The benchmark report could not be read.") from error
