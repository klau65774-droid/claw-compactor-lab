"""Small synthetic workbench presets and repeatable audit inputs."""

from __future__ import annotations

import json

from ._contract import CompressionMode, ContentTypeHint, MessageRole, PresetItem


def scale_text(blocks: int = 400) -> str:
    # Same block shape and vocabulary as the upstream CPU reproduction.
    vocabulary = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey", "xray", "yankee", "zulu", "latency", "throughput", "gateway", "shard", "worker", "token", "cache", "prefix", "context", "router", "schema", "stream", "buffer", "metric"]
    return "\n\n".join(
        " ".join(
            f"{vocabulary[(i * 7 + j * 13 + (j % 5) * i) % len(vocabulary)]}_{i % 19}_{j % 7}"
            for j in range(90)
        )
        for i in range(blocks)
    )


def chinese_rag(blocks: int = 12) -> str:
    base = (
        "杭州高并发网关节点在上午十点三十分触发熔断降级保护，"
        "上游模型推理队列积压达到阈值，请立即核查连接池状态与缓存命中率并保持重试退避。"
    )
    return "\n\n".join(f"{base}批次编号{i % 3 + 1}。" for i in range(blocks))


def chinese_json(size: int = 35) -> str:
    records: list[dict[str, object]] = [
        {"id": i, "zone": "杭州节点", "status": "正常", "message": "服务健康检查通过"}
        for i in range(size)
    ]
    records[size // 3] = {"id": size // 3, "meta": {"detail": [{"status_code": 502, "message": "上游失败"}]}}
    records[size * 2 // 3] = {"id": size * 2 // 3, "events": [{"severity": "严重异常", "module": "结算"}]}
    return json.dumps(records, ensure_ascii=False, separators=(",", ":"))


def python_traceback(frames: int = 48) -> str:
    lines = ["Traceback (most recent call last):"]
    for index in range(frames):
        lines.append(f'  File "/app/workers/stage_{index}.py", line {100 + index}, in execute_step')
        lines.append(f"    return dispatch_downstream(payload_batch_index={index}, retry_budget=3)")
    lines.append("ZeroDivisionError: division by zero in compute_margin")
    return "\n".join(lines)


def presets() -> list[PresetItem]:
    def preset(
        id: str, name: str, subtitle: str, description: str,
        role: MessageRole, kind: ContentTypeHint, mode: CompressionMode,
        target: int, query: str, text: str,
    ) -> PresetItem:
        return PresetItem(
            id=id, name=name, subtitle=subtitle, description=description, role=role,
            content_type=kind, mode=mode, target_budget_tokens=target,
            query=query, text=text, redact_secrets=True,
        )

    diff = (
        "Deploy artifacts from staging to production.\n"
        "User is not authorized for root access in the billing service.\n"
        "| key | value |\n| --- | --- |\n| mode | active |\n"
        "--- a/src/cache.py\n+++ b/src/cache.py\n@@ -1 +1 @@\n"
        "-cache_ttl = 0\n+cache_ttl = 60\n"
    )
    prompt = (
        "System active from 2026-01-15 to 2026-06-30.\n"
        "Use provider key sk-proj-ContextLabDemoOnly0123456789 and gateway token "
        "eyJjb250ZXh0LWxhYiJ9.eyJkZW1vIjp0cnVlfQ.synthetic_signature.\n"
        "Preserve the release window and report status."
    )
    evidence = (
        "杭州可用区生产集群承诺SLA为99.95%，超时阈值锁定在1500ms。\n\n"
        "网关缓存周期性刷新配置。开发团队同步了发布计划。"
        "常规测试完成以后，维护窗口按原计划结束。"
    )
    return [
        preset(
            "bilingual_rag_dedup", "Bilingual RAG Deduplication", "Near-duplicate Chinese retrieval blocks",
            "Compare bilingual shingling with the upstream English-only block deduplicator.",
            "user", "text", "balanced", 450, "网关熔断 缓存命中率", chinese_rag(),
        ),
        preset(
            "cn_json_tool_errors", "Chinese JSON Tool Errors", "Compact Unicode and nested failures",
            "Check whether sampling preserves a nested HTTP 502 record and a Chinese exception.",
            "tool", "json", "balanced", 600, "502 上游失败 严重异常", chinese_json(),
        ),
        preset(
            "kv_cache_secret_prompt", "KV-Cache & Secret Leak Prompt", "Dates and synthetic credentials",
            "Measure raw-echo behavior and independent placeholders for two release dates.",
            "system", "text", "lossless", 200, "release window", prompt,
        ),
        preset(
            "python_traceback_budget", "Python Traceback Tail Survival", "Head, evidence and root cause",
            "Check whether the final exception and bottom frame survive tool-result compression.",
            "tool", "log", "balanced", 400, "ZeroDivisionError compute_margin", python_traceback(),
        ),
        preset(
            "swebench_diff_code", "SWE-bench Diff & Markdown Table", "Unified diff and Markdown layout",
            "Compare the original diff lines, table delimiters, and a negated access condition.",
            "user", "diff", "balanced", 300, "cache_ttl", diff,
        ),
        preset(
            "longbench_zh_needle", "LongBench-ZH Needle Retention", "Space-free Chinese prose",
            "Retain the availability zone, SLA and latency threshold under a shorter budget.",
            "user", "text", "aggressive", 80, "杭州可用区 SLA 99.95% 1500ms", evidence,
        ),
    ]
