"""Security, structure and budget gates in the CPU pipeline."""

import json

from claw_compactor.enhanced._contract import CompressCompareRequest
from claw_compactor.enhanced._fixtures import python_traceback
from claw_compactor.enhanced._text import count_tokens, structure_preserved
from claw_compactor.enhanced.deterministic_ionizer import BoundedRewindStore
from claw_compactor.enhanced.monotonic_pipeline import EnhancedCompactorEngine
from claw_compactor.enhanced.safe_quantum_lock import SafeQuantumLockStage
from claw_compactor.enhanced.smart_tool_budget import smart_budget_tool_results
from claw_compactor.enhanced.structure_nexus import StructureNexusStage, compact_code
from claw_compactor.fusion.base import FusionContext
from claw_compactor.rewind.marker import extract_markers

_KEY = "sk-proj-UnitTestOnlyCookieK8QfX0w5"


def test_quantum_lock_indexes_distinct_values_and_never_echoes_the_secret():
    text = f"2026-01-15 → 2026-06-30. 密钥{_KEY}。Repeat 2026-01-15."
    locked = SafeQuantumLockStage().stabilize(text)
    assert locked.prefix.count("<date_1>") == 2 and locked.prefix.count("<date_2>") == 1
    assert "<date_1>: 2026-01-15" in locked.appendix
    assert "<date_2>: 2026-06-30" in locked.appendix
    assert f"{_KEY[:4]}***{_KEY[-4:]} [REDACTED]" in locked.appendix
    assert _KEY not in locked.text
    occupied = SafeQuantumLockStage().stabilize("<date_1> literal, 2026-01-15 real")
    assert occupied.prefix == "<date_1> literal, <date_2> real"


def test_rollback_does_not_restore_raw_credentials():
    src = f"Date 2026-01-15. {_KEY}"
    outcome = EnhancedCompactorEngine().compress(CompressCompareRequest(
        text=src, role="system", content_type="text", redact_secrets=True, deterministic_seed=2, mode="lossless",
    ))
    assert outcome.stages[-1].rolled_back
    assert outcome.compressed_tokens <= outcome.original_tokens
    assert outcome.compressed_text == "Date 2026-01-15. <secret_1>"
    assert outcome.secret_leaks_count == 0
    assert all(step.compressed_tokens <= step.original_tokens for step in outcome.stages)


def test_escaped_json_credential_is_also_removed_before_rewind():
    text = r'["\u0073\u006b-proj-UnitTestOnlyCookieK8QfX0w5",' + ",".join(
        json.dumps(f"healthy service result row {i}") for i in range(48)
    ) + "]"
    engine = EnhancedCompactorEngine()
    outcome = engine.compress(CompressCompareRequest(
        text=text, role="tool", content_type="json", redact_secrets=True, deterministic_seed=0, mode="balanced",
    ))
    ref, = outcome.rewind_markers
    original = engine.rewind_store.retrieve(ref.hash_id)
    assert original is not None and _KEY not in original and r"\u0073" not in original
    assert json.loads(original)[0] == "<secret_1>"
    assert _KEY not in outcome.compressed_text and outcome.secret_leaks_count == 0


def test_structure_nexus_retains_exact_layout_and_protected_facts():
    fence = chr(96) * 3
    protected = [
        "Move artifacts from staging to production.",
        "User is not authorized in billing.",
        "禁止删除审计记录，延迟上限1500ms，承诺99.95%。",
        "| key | value |", "| --- | --- |", "| mode | active |",
        f"{fence}python", "result = report('a value')", fence,
        "--- a/src/cache.py", "+++ b/src/cache.py", "@@ -1 +1 @@", "-ttl = 0", "+ttl = 60",
    ]
    lines = ["Documentation repeats general background. Extra detail appears here."] + protected
    text = "\r\n".join(lines) + "\r\n"
    result = StructureNexusStage().apply(FusionContext(text, token_budget=40, query="billing"))
    assert result.content.count("\r\n") == text.count("\r\n")
    for line in protected:
        assert line in result.content.splitlines()
    assert result.compressed_tokens < result.original_tokens


def test_structure_nexus_prunes_space_free_chinese_around_query_evidence():
    text = (
        "日常例行维护已经按照计划完成。发布人员同步工单并确认工单流转。"
        "杭州可用区生产集群承诺SLA为99.95%。"
        "例行的心跳采样由另一个独立服务处理。不相关说明可以延后。"
    )
    outcome = StructureNexusStage(aggressive=True).apply(
        FusionContext(text, token_budget=44, query="杭州可用区 SLA")
    )
    assert 0 < outcome.compressed_tokens < outcome.original_tokens
    assert "杭州可用区生产集群承诺SLA为99.95%" in outcome.content


def test_tool_budget_preserves_head_query_and_traceback_tail_and_rewinds():
    original = python_traceback(240).replace(
        '  File "/app/workers/stage_90.py"',
        'database shard Hangzhou-primary timed out\n  File "/app/workers/stage_90.py"',
    )
    messages = [
        {"role": "tool", "name": "python", "content": original},
        {"role": "tool", "name": "other", "content": "ok"},
    ]
    store = BoundedRewindStore()
    results, stats = smart_budget_tool_results(
        messages, keep_recent=1, max_tokens_per_result=140, query="Hangzhou primary", rewind_store=store,
    )
    output = results[0]["content"]
    assert output.startswith("Traceback (most recent call last):")
    assert "Hangzhou-primary timed out" in output
    assert 'File "/app/workers/stage_239.py"' in output
    assert "ZeroDivisionError: division by zero in compute_margin" in output
    assert count_tokens(output) <= 140
    marker, = extract_markers(output)
    assert store.retrieve(marker.hash_id) == original
    assert messages[0]["content"] == original and stats["tool_results_truncated"] == 1


def test_structure_nexus_handles_abbreviations_cn_units_and_right_adjacent_elaborations():
    text = (
        "Background filler explains historical context without answering anything useful. "
        "George H. W. Bush served with Richard Darman in the administration. "
        "Xcel Energy Inc. is a U.S. regulated electric utility in Minnesota. "
        "Fomento Económico Mexicano, S.A.B. de C.V. operates beverages in Mexico. "
        "Mark Neary Donohue Jr. was an American race car driver. "
        "We introduce an invertibility condition for our neural projector. "
        "Specifically, we constrain our neural projector with two requirements: (1) A and (2) B. "
        "We built the dataset by collecting 3,268 random Facebook status update messages. "
        "2010年社会消费品零售总额达到51亿元，是2005年的2.2倍。 "
        "Another filler sentence describes routine clerical schedules."
    )
    out = StructureNexusStage().apply(
        FusionContext(
            text, token_budget=int(count_tokens(text) * 0.85),
            query="What is the invertibility condition and president beverage utility driver?",
        )
    )
    assert "George H. W. Bush" in out.content
    assert "Xcel Energy Inc." in out.content
    assert "Fomento Económico Mexicano, S.A.B. de C.V." in out.content
    assert "Mark Neary Donohue Jr." in out.content
    assert "Specifically, we constrain our neural projector with two requirements" in out.content
    assert "3,268 random Facebook status update messages" in out.content
    assert "是2005年的2.2倍" in out.content


def test_compact_code_strips_prefix_comments_while_preserving_tail_and_executables():
    header = (
        "/* Copyright banner line 1\n"
        " * Copyright banner line 2\n"
        " */\n"
        "// Generated utility comments\n"
        "// Another explanatory comment\n\n\n"
        "using System;"
    )
    body = "\n".join(f"        int x{i} = {i};" for i in range(30))
    tail = "        // tail comment must stay\n        return x29;"
    source = f"{header}\n{body}\n{tail}"
    compacted = compact_code(source, tail_protect_lines=25)
    assert count_tokens(compacted) < count_tokens(source)
    assert "// tail comment must stay" in compacted
    assert "return x29;" in compacted
    assert "using System;" in compacted
    assert structure_preserved(source, compacted, "code")

    lossless = EnhancedCompactorEngine().compress(CompressCompareRequest(
        text=source, content_type="code", role="user", redact_secrets=True, deterministic_seed=0, mode="lossless",
    ))
    assert lossless.compressed_text == source


def test_structure_nexus_protects_acronyms_citations_and_morphological_queries() -> None:
    tags = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet")
    fillers = [
        f"Introductory prose segment {tag} covers unrelated editorial framing lacking empirical commentary."
        for tag in tags
    ]
    source = "\n".join([
        fillers[0],
        fillers[1],
        "The Wall Street Journal (WSJ) corpus provides clean syntactic trees.",
        fillers[2],
        "We adopt the Maximum Entropy Model (MEM) from prior work.",
        fillers[3],
        "Prior citation Rei2016 introduced a character-level neural objective.",
        fillers[4],
        "The proposed error detection system flags subtle grammatical anomalies.",
        fillers[5],
        "Multiple benchmark dataset splits validate robustness across domains.",
        fillers[6],
        "The Diachronic Usage Relatedness (DURel) standard tracks semantic shift.",
        fillers[7],
        "A semi-character recurrent module (ScRNN) predicts spelling corrections cleanly.",
        fillers[8],
        fillers[9],
    ])
    outcome = StructureNexusStage(aggressive=False).apply(
        FusionContext(
            source,
            token_budget=130,
            query="Which datasets evaluate the semicharacter architecture?",
        )
    )
    assert 0 < outcome.compressed_tokens < outcome.original_tokens
    assert "(WSJ)" in outcome.content
    assert "(MEM)" in outcome.content
    assert "(DURel)" in outcome.content
    assert "(ScRNN)" in outcome.content
    assert "Rei2016" in outcome.content
    assert "error detection system" in outcome.content
    assert "dataset" in outcome.content
    assert "semi-character" in outcome.content

