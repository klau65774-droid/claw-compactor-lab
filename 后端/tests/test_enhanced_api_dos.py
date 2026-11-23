"""Contract, streaming, operational, and synthetic OOD checks."""

import json

import pytest
from claw_compactor.enhanced import api
from claw_compactor.enhanced._fixtures import chinese_json, python_traceback
from fastapi.testclient import TestClient

client = TestClient(api.app)

_OODS = [
    "sk-projectmodel-fixture00AdjXa71", "sk-proj-FixtureOmegaMix50axq8",
    "sk-svcacct-LocalTestNoAccountF8aS2", "rk-FixtureApi1000xy_Z9B2",
    "LTAIfixtureR8QcL0tAd01", "LTAITrainingFixtureK258H4299997J",
    "AKIA1FIXTUREONLY1111", "ASIA123456FICTIVE135",
    "ghp_DemoOnly8bRy11Bb3390Pp76", "gho_FixtureOnly99zz827100Rr001",
    "github_pat_LocalOnly2026_AaR4Hu98N2",
    "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0In0.fake-signature",
    "eyJ0eXAiOiJKV1QifQ.eyJpYXQiOjE3MDAwMDAwMDB9.numbered_test",
    "13800007426", "+86-15600006759",
]
_DECOYS = [
    "85e28c19-ef1d-4ca9-a83c-15301234567f",
    "31646b02-3800-4c16-a465-220ec9aa7abf",
    "b125c8590af1f4c06b7865d987aa25473ecd071051bf4e4ecec6fdbba6292962",
    "138000074261e9b811f3ec7e518b91a99849b76635cdd3114336742d6db0f8c",
    "2026-01-15", "2026-06-30T10:12:01Z", "10:15:30", "1764528109000",
    "110105194912310029", "订单138000074260312220",
    "Report Bearer scopes for nonproduction documentation", "Bearer documentation example.",
    "sk-demo-label", "gho_example", "LTAI abbreviates a test region",
]


def _compare_body(text, *, role="tool", kind="json", budget=600):
    return {
        "text": text, "role": role, "content_type": kind, "target_budget_tokens": budget,
        "mode": "balanced", "redact_secrets": True, "deterministic_seed": 42,
    }


def test_health_and_six_presets():
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["upstream_version"] == "7.1.0"
    assert health.json()["upstream_commit"].startswith("c1b936d40b11")
    response = client.get("/api/v1/presets")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["presets"]} == {
        "bilingual_rag_dedup", "cn_json_tool_errors", "kv_cache_secret_prompt",
        "python_traceback_budget", "swebench_diff_code", "longbench_zh_needle",
    }


def test_compare_uses_original_upstream_and_returns_rewind_references():
    source = chinese_json()
    response = client.post("/api/v1/compress/compare", json=_compare_body(source))
    assert response.status_code == 200
    data = response.json()
    upstream, enhanced = data["upstream"], data["enhanced"]
    assert upstream["engine"] == "upstream"
    assert any(stage["name"] == "ionizer" and not stage["skipped"] for stage in upstream["stages"])
    assert enhanced["engine"] == "enhanced" and enhanced["verdict"] == "PASSED"
    assert data["token_delta"] == upstream["compressed_tokens"] - enhanced["compressed_tokens"]
    ref, = enhanced["rewind_markers"]
    result = client.post("/api/v1/rewind/retrieve", json={"hash_id": ref["hash_id"]})
    assert result.status_code == 200 and result.json()["found"]
    assert result.json()["original_text"] == source
    assert result.json()["compressed_tokens"] == enhanced["compressed_tokens"]
    missing = client.post("/api/v1/rewind/retrieve", json={"hash_id": "f" * 24})
    assert missing.json()["evicted_or_expired"] and missing.json()["original_text"] is None


def test_stream_events_follow_actual_metrics_and_match_unary():
    text = python_traceback(100)
    body = _compare_body(text, kind="log", budget=200)
    response = client.post("/api/v1/compress/stream", json=body)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = []
    for frame in response.text.strip().split("\n\n"):
        event, data = frame.split("\n")
        events.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    assert [name for name, _ in events].count("milestone") == 1
    assert events[0][0] == "stage" and events[-1][0] == "complete"
    progress = [data["progress_pct"] for event, data in events[:-1] if event == "stage"]
    assert progress == sorted(progress) and progress[0] == 0 and progress[-1] == 100
    unary = client.post("/api/v1/compress/compare", json=body).json()
    assert events[-1][1]["enhanced"]["compressed_text"] == unary["enhanced"]["compressed_text"]
    assert "ZeroDivisionError" in unary["enhanced"]["compressed_text"]


def test_kv_simulate_runs_actual_sampling_and_preserves_tail():
    messages = [
        {"role": "system", "content": " ".join(["Start 2026-01-15, till 2026-06-30."] * 18)},
        {"role": "tool", "content": chinese_json(60), "name": "db_tool"},
        {"role": "tool", "content": python_traceback(240), "name": "python_tool"},
    ]
    response = client.post("/api/v1/kv-cache/simulate", json={
        "runs": 10, "messages": messages, "keep_recent": 2, "max_tokens_per_result": 2500,
        "query": "compute_margin 严重异常", "redact_secrets": True,
    })
    assert response.status_code == 200
    baseline, enhanced = (response.json()[name] for name in ("upstream", "enhanced"))
    assert baseline["unique_hashes"] > 1 and not baseline["tail_exception_retained"]
    assert len(enhanced["hashes"]) == 10 and len(set(enhanced["hashes"])) == enhanced["unique_hashes"] == 1
    assert enhanced["prefix_hit_rate_pct"] == 100
    assert enhanced["tail_exception_retained"] and enhanced["indexed_placeholders_ok"]
    assert enhanced["secret_leaks_count"] == 0


@pytest.mark.parametrize("fixture", _OODS)
def test_security_hits_all_15_unseen_synthetic_credentials(fixture):
    response = client.post("/api/v1/security/redact", json={
        "text": f"供应商记录：{fixture}，请勿输出。", "redact_secrets": True, "index_placeholders": True,
    })
    assert response.status_code == 200
    assert fixture not in response.text
    result = response.json()["enhanced"]
    assert result["verdict"] == "SAFE" and not result["raw_secret_echoed"]
    assert result["findings"][0]["count"] == 1 and result["findings"][0]["placeholder"] == "<secret_1>"
    assert result["findings"][0]["masked_preview"].endswith("[REDACTED]")


@pytest.mark.parametrize("fixture", _DECOYS)
def test_security_has_zero_false_positives_on_15_benign_decoys(fixture):
    response = client.post("/api/v1/security/redact", json={
        "text": fixture, "redact_secrets": True, "index_placeholders": True,
    })
    assert response.status_code == 200
    assert response.json()["enhanced"]["findings"] == []


def test_raw_upstream_echo_is_measured_before_safe_transport():
    raw = _OODS[1]
    response = client.post("/api/v1/security/redact", json={
        "text": f"Key {raw} from 2026-01-15 to 2026-06-30.", "redact_secrets": True, "index_placeholders": True,
    })
    report = response.json()
    assert report["upstream"]["verdict"] == "VULNERABLE"
    assert report["upstream"]["raw_secret_echoed"] and not report["enhanced"]["raw_secret_echoed"]
    assert "<date_1>" in report["enhanced"]["stabilized_text"] and "<date_2>" in report["enhanced"]["stabilized_text"]
    assert raw not in response.text
    invalid = client.post("/api/v1/compress/compare", json=_compare_body("hello", role=raw))
    assert invalid.status_code == 422 and raw not in invalid.text
    oversized = client.post("/api/v1/compress/compare", json=_compare_body("x" * 1_048_576))
    assert oversized.status_code == 413


def test_special_token_spellings_and_request_metadata_are_safe_to_compare():
    body = _compare_body("Output mentions <|endoftext|> literally.", kind="log")
    body["preset_id"] = _OODS[1]
    response = client.post("/api/v1/compress/compare", json=body)
    assert response.status_code == 200
    assert _OODS[1] not in response.text
    assert response.json()["enhanced"]["compressed_text"] == body["text"]


def test_dos_audit_live_checks():
    response = client.post("/api/v1/dos/audit", json={})
    assert response.status_code == 200
    report = response.json()
    assert {r["id"] for r in report["audits"]} == {
        "semantic_dedup_scale", "deep_json_nesting", "rewind_store_lru_cap", "non_expansion_rollback",
    }
    assert report["passed"] == report["total"] == 4, [
        row for row in report["audits"] if row["status"] == "FAIL"
    ]
    assert all(row["latency_ms"] > 0 for row in report["audits"])


def test_benchmark_route_reports_missing_and_serves_verified_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / "benchmarks_report.json"
    monkeypatch.setattr(api, "BENCHMARK_REPORT", artifact)
    response = client.get("/api/v1/benchmarks/summary")
    assert response.status_code == 200 and response.json()["status"] == "preview"
    payload = response.json()
    payload["status"] = "verified"
    payload["generated_at"] = "2026-11-23T00:00:00Z"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    verified = client.get("/api/v1/benchmarks/summary")
    assert verified.status_code == 200 and verified.json() == payload
