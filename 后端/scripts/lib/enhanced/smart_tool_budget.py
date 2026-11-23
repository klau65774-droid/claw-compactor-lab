"""Token-aware tool excerpts with query evidence and traceback root causes."""

from __future__ import annotations

import re
from typing import Any

from ..fusion.base import FusionResult
from ..fusion.tool_result_budget import EXEMPT_TOOLS
from ..rewind.marker import embed_marker
from ._text import count_tokens, digest, query_terms, shingles
from .deterministic_ionizer import BoundedRewindStore

_EXCEPTION = re.compile(
    r"^\s*(?:[\w.]*?(?:Error|Exception|ExceptionGroup|Warning)|KeyboardInterrupt|SystemExit)"
    r"(?::.*)?\s*$",
)
_FRAME = re.compile(r'^\s*File ".+", line \d+')


def _tail_indices(lines: list[str]) -> list[int]:
    for index in range(len(lines) - 1, -1, -1):
        if _EXCEPTION.match(lines[index]):
            for frame in range(index - 1, -1, -1):
                if _FRAME.match(lines[frame]):
                    return list(range(frame, min(frame + 3, index))) + [index]
                if index - frame > 8:
                    break
            return [index]
    return [i for i in range(max(0, len(lines) - 2), len(lines)) if lines[i].strip()]


def traceback_tail(text: str) -> str:
    for line in reversed(text.splitlines()):
        if _EXCEPTION.match(line):
            return line.strip()
    return ""


def compact_tool_result(
    text: str, *, max_tokens: int, query: str | None = None,
    rewind_store: BoundedRewindStore | None = None,
) -> FusionResult:
    original = count_tokens(text)
    unchanged = FusionResult(text, original, original, skipped=True)
    if original <= max_tokens:
        return unchanged
    lines = text.splitlines()
    if not lines:
        return unchanged
    tail = _tail_indices(lines)
    kept = set(tail) | {0}
    terms = query_terms(query)
    hash_id = digest(text)[:24] if rewind_store is not None else ""

    def render() -> str:
        body = "\n".join(lines[i] for i in sorted(kept))
        if hash_id:
            return embed_marker(body, len(lines), len(kept), hash_id)
        return body

    # Keep the full cause and bottom frame even if they alone exceed the
    # requested budget. Other traceback context competes for the remainder.
    protected = {tail[0], tail[-1]} if tail else set()
    for index in [*tail[1:-1], 0]:
        if count_tokens(render()) <= max_tokens:
            break
        if index not in protected:
            kept.discard(index)

    ranked: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if index in kept or not line.strip():
            continue
        score = len(terms & shingles(line, cjk_unigrams=False)) if terms else 0
        if score or index < 3:
            ranked.append((-score, index))
    ranked.sort()

    used = count_tokens(render())
    optional: list[int] = []
    for _, index in ranked:
        cost = count_tokens(lines[index]) + 1
        if used + cost <= max_tokens:
            kept.add(index)
            optional.append(index)
            used += cost
    output = render()
    tokens = count_tokens(output)
    while optional and tokens > max_tokens:
        kept.remove(optional.pop())
        output = render()
        tokens = count_tokens(output)

    if tokens >= original:
        return unchanged
    if rewind_store is not None:
        if rewind_store.store(text, output, original, tokens) is None:
            return unchanged
        return FusionResult(output, original, tokens, markers=[hash_id])
    return FusionResult(output, original, tokens)


def smart_budget_tool_results(
    messages: list[dict[str, Any]], keep_recent: int = 5, max_tokens_per_result: int = 8000, *,
    query: str | None = None, rewind_store: BoundedRewindStore | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    indices = [i for i, msg in enumerate(messages) if msg.get("role") == "tool"]
    old_count = max(0, len(indices) - max(0, keep_recent))
    output = list(messages)
    stats = {
        "tool_results_total": len(indices),
        "tool_results_truncated": 0,
        "tool_results_oversized": 0,
        "tokens_saved": 0,
        "chars_saved": 0,
    }
    for rank, index in enumerate(indices):
        msg = messages[index]
        content = msg.get("content", "")
        name = msg.get("name") or msg.get("tool_call_id") or ""
        if not isinstance(content, str) or any(exempt in str(name).lower() for exempt in EXEMPT_TOOLS):
            continue
        old = rank < old_count
        budget = min(160, max_tokens_per_result) if old else max_tokens_per_result
        result = compact_tool_result(content, max_tokens=budget, query=query, rewind_store=rewind_store)
        if not result.skipped:
            output[index] = {**msg, "content": result.content}
            stats["tool_results_truncated" if old else "tool_results_oversized"] += 1
            stats["tokens_saved"] += result.original_tokens - result.compressed_tokens
            stats["chars_saved"] += len(content) - len(result.content)
    return output, stats
