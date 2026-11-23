"""Bilingual deduplication, deterministic sampling, and rewind regression tests."""

import json
from dataclasses import replace

from claw_compactor.enhanced._audit import dedup_timings
from claw_compactor.enhanced._fixtures import chinese_json, chinese_rag
from claw_compactor.enhanced._text import digest
from claw_compactor.enhanced.bilingual_dedup import BilingualLshDedupStage
from claw_compactor.enhanced.deterministic_ionizer import (
    BoundedRewindStore,
    DeterministicIonizerStage,
    contains_error,
)
from claw_compactor.fusion.base import FusionContext
from claw_compactor.rewind.marker import extract_markers


def test_cjk_near_duplicates_reduce_tokens_without_removing_new_sla():
    text = chinese_rag()
    ctx = FusionContext(text, query="熔断 缓存命中率")
    stage = BilingualLshDedupStage()
    result = stage.apply(ctx)
    assert result.compressed_tokens <= result.original_tokens * 0.20
    assert result.content.count("杭州高并发") == 1
    assert "缓存命中率" in result.content

    prefix = text.split("\n\n", 1)[0]
    distinct = f"{prefix} SLA 99.95%\n\n{prefix} SLA 98.95%"
    assert stage.apply(replace(ctx, content=distinct)).content == distinct


def test_english_dedup_scales_subquadratically_and_beats_real_upstream():
    upstream, half, full = dedup_timings()
    assert full < half * 3.2
    assert upstream / full > 20


def test_ionizer_is_deterministic_and_rewinds_the_exact_source():
    source = chinese_json(60)
    store = BoundedRewindStore()
    ctx = FusionContext(source, content_type="json", role="tool")
    results = [
        DeterministicIonizerStage(store, deterministic_seed=42).apply(ctx)
        for _ in range(10)
    ]
    assert len({digest(result.content) for result in results}) == 1
    result = results[0]
    clean_json = result.content.rsplit("\n", 1)[0]
    records = json.loads(clean_json)
    assert len(records) < 60
    assert result.compressed_tokens < result.original_tokens
    assert r"\u" not in clean_json
    assert any(record.get("meta", {}).get("detail", [{}])[0].get("status_code") == 502 for record in records)
    assert any(record.get("events", [{}])[0].get("severity") == "严重异常" for record in records)
    marker, = extract_markers(result.content)
    assert marker.original_count == 60
    assert marker.compressed_count == len(records)
    assert store.retrieve(marker.hash_id) == source


def test_error_scanner_bounds_depth_and_preserves_error_records():
    assert contains_error({"outer": [{"nested": {"status_code": "503"}}]})
    assert contains_error({"events": [{"msg": "请求已触发告警"}]})
    assert not contains_error({"outer": [{"nested": {"status_code": 302}}]})
    deep: object = "失败"
    for _ in range(128):
        deep = [deep]
    assert contains_error(deep)
    source = "[" * 2048 + "0" + "]" * 2048
    result = DeterministicIonizerStage().apply(FusionContext(source, content_type="json"))
    assert result.content == source and result.skipped
    assert result.warnings == ["JSON depth exceeds 32"]


def test_rewind_counts_utf8_bytes_and_refreshes_access_order():
    store = BoundedRewindStore(max_entries=2, max_bytes=20)
    first = store.store("杭州", "a")
    second = store.store("台北", "b")
    assert first and second and store.total_bytes == 14
    assert store.retrieve(first) == "杭州"
    third = store.store("广州", "ccccccc")
    assert third and store.retrieve(second) is None and store.total_bytes == 20
    assert store.store("北京", "x" * 22) is None
    assert store.retrieve(first) == "杭州"
    assert store.store("广州", "c") == third
    assert store.total_bytes == 14 and store.size == 2


def test_rewind_expires_entries_with_byte_accounting(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("claw_compactor.enhanced.deterministic_ionizer.time.monotonic", lambda: clock[0])
    store = BoundedRewindStore(ttl_seconds=2)
    key = store.store("original", "short")
    assert key
    clock[0] += 3
    assert store.get(key) is None and store.size == 0 and store.total_bytes == 0
