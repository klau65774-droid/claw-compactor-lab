"""Query-aware, CPU-only pruning within bilingual prose and code files."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..fusion.base import FusionContext, FusionResult, FusionStage
from ._text import (
    FACT,
    count_tokens,
    is_strippable_code_comment_line,
    layout_mask,
    query_terms,
    shingles,
    split_lines,
)

_SENTENCE_BOUNDARY = re.compile(r"[。！？；]|[.!?;](?=[ \t]+|$)")
_INITIAL_DOT = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]\.){1,6}$")
_WORD_DOT = re.compile(r"([A-Za-z]+)\.$")
_ABBREVIATIONS = frozenset({
    "inc", "ltd", "corp", "co", "jr", "sr", "dr", "mr", "mrs", "ms", "prof",
    "st", "vs", "no", "fig", "eq", "al", "approx", "est", "dept", "gov",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})
_QUERY_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "did", "do",
    "does", "for", "from", "had", "has", "have", "he", "her", "his", "how", "i",
    "in", "is", "it", "its", "many", "may", "much", "of", "on", "or", "our",
    "she", "should", "that", "the", "their", "them", "they", "this", "those",
    "these", "to", "was", "we", "were", "what", "when", "where", "which", "who",
    "whom", "whose", "why", "will", "with", "would", "you", "your",
})
_FILLER = re.compile(
    r"^(?:\s*(?:it is worth noting that|for the sake of completeness|as a general reminder)"
    r"\b[:,]?\s*)",
    re.IGNORECASE,
)
_TECH_EVIDENCE = re.compile(
    r"\((?=[A-Za-z0-9-]{2,12}\))(?=[A-Za-z0-9-]*[A-Z][A-Za-z0-9-]*[A-Z])[A-Za-z0-9-]{2,12}\)"
    r"|(?i:\b[a-z]{2,20}(?:19|20)\d{2}\b|\b(?:error|exception|failed|failure|timeout)\b)",
    re.ASCII,
)
_HYPHEN_COMPOUND = re.compile(r"\b[A-Za-z]{2,}(?:-[A-Za-z]{2,})+\b")


def _stem_terms(terms: set[str]) -> set[str]:
    stemmed: set[str] = set()
    for term in terms:
        stemmed.add(term)
        if term.isascii() and term.isalpha() and len(term) >= 5:
            for suffix in ("ing", "ed", "es", "s"):
                if term.endswith(suffix) and len(term) - len(suffix) >= 3:
                    stemmed.add(term[: -len(suffix)])
                    break
    return stemmed


def _clause_terms(text: str, *, base: set[str] | None = None) -> set[str]:
    raw = set(shingles(text, cjk_unigrams=False) if base is None else base)
    raw.update(match.group(0).replace("-", "").lower() for match in _HYPHEN_COMPOUND.finditer(text))
    return _stem_terms(raw)


def _split_clauses(line: str) -> list[str]:
    """Split a prose line on sentence boundaries without breaking abbreviations."""
    parts: list[str] = []
    cursor = 0
    for match in _SENTENCE_BOUNDARY.finditer(line):
        end = match.end()
        if match.group(0) == ".":
            prefix = line[cursor:end]
            if _INITIAL_DOT.search(prefix):
                continue
            word_match = _WORD_DOT.search(prefix)
            if word_match and word_match.group(1).lower() in _ABBREVIATIONS:
                continue
        parts.append(line[cursor:end])
        cursor = end
    if cursor < len(line):
        parts.append(line[cursor:])
    return [part for part in parts if part]


def compact_code(text: str, *, tail_protect_lines: int = 25) -> str:
    """Strip standalone comments and excess blank lines in code prefixes safely."""
    lines = split_lines(text)
    if len(lines) <= tail_protect_lines:
        return text
    prefix_limit = len(lines) - tail_protect_lines
    kept: list[tuple[str, str]] = []
    in_block_comment = False
    triple_delim: str | None = None

    for idx, (body, ending) in enumerate(lines):
        if idx >= prefix_limit:
            kept.append((body, ending))
            continue

        stripped = body.lstrip()
        if triple_delim is not None:
            kept.append((body, ending))
            if body.count(triple_delim) % 2 == 1:
                triple_delim = None
            continue

        if in_block_comment:
            close_pos = stripped.find("*/")
            if close_pos == -1:
                continue
            in_block_comment = False
            after = stripped[close_pos + 2 :].strip()
            if not after:
                continue
            kept.append((after, ending))
            continue

        if stripped.startswith("/*"):
            close_pos = stripped.find("*/", 2)
            if close_pos == -1:
                in_block_comment = True
                continue
            after = stripped[close_pos + 2 :].strip()
            if not after:
                continue
            kept.append((after, ending))
            continue

        for quote in ('"""', "'''"):
            if body.count(quote) % 2 == 1:
                triple_delim = quote
                break
        if triple_delim is not None:
            kept.append((body, ending))
            continue

        if is_strippable_code_comment_line(body):
            continue

        trimmed = body.rstrip(" \t")
        if not trimmed and kept and not kept[-1][0]:
            continue
        kept.append((trimmed, ending))

    return "".join(body + ending for body, ending in kept)


@dataclass(frozen=True)
class _Clause:
    line: int
    text: str
    weight: int
    relevance: int
    removable: bool


class StructureNexusStage(FusionStage):
    name = "structure_nexus"
    order = 30

    def __init__(self, *, aggressive: bool = False) -> None:
        self.aggressive = aggressive

    def should_apply(self, ctx: FusionContext) -> bool:
        return ctx.content_type in {"text", "search", "code"} and len(ctx.content) >= 64

    def apply(self, ctx: FusionContext, *, original_tokens: int | None = None) -> FusionResult:
        original = count_tokens(ctx.content) if original_tokens is None else original_tokens
        if not original:
            return FusionResult(ctx.content, original, original, skipped=True)
        if ctx.content_type == "code":
            compressed = compact_code(ctx.content)
            if compressed == ctx.content:
                return FusionResult(ctx.content, original, original, skipped=True)
            return FusionResult(
                compressed, original, count_tokens(compressed),
                markers=["structure_nexus:code_comments_pruned"],
            )

        lines = split_lines(ctx.content)
        layout = layout_mask([line for line, _ in lines])
        raw_query = query_terms(ctx.query)
        filtered_query = {term for term in raw_query if term not in _QUERY_STOPWORDS}
        query = _clause_terms(ctx.query or "", base=filtered_query or raw_query) if raw_query else set()
        target = ctx.token_budget
        if target is None and self.aggressive and query:
            target = int(original * 0.68)

        clauses: list[_Clause] = []
        removed: set[int] = set()
        seen: set[str] = set()
        for index, (line, _) in enumerate(lines):
            parts = [line] if layout[index] else _split_clauses(line)
            for part in parts:
                if not part:
                    continue
                fixed = (
                    layout[index]
                    or bool(FACT.search(part))
                    or "<secret" in part
                    or (not self.aggressive and bool(_TECH_EVIDENCE.search(part)))
                )
                relevance = len(query & _clause_terms(part)) if query else 0
                if not fixed and not relevance:
                    part = _FILLER.sub("", part)
                trimmed = part.strip()
                if not fixed and not relevance and len(trimmed) >= 24 and trimmed in seen:
                    removed.add(len(clauses))
                seen.add(trimmed)
                clauses.append(_Clause(index, part, len(part), relevance, not fixed and not relevance))

        candidates = [
            i for i, clause in enumerate(clauses) if clause.removable and i not in removed
        ]
        if target is not None and 0 <= target < original and candidates:
            right_adjacent: set[int] = {
                i for i in candidates
                if i > 0
                and clauses[i - 1].relevance > 0
                and clauses[i].line - clauses[i - 1].line <= 1
            }

            def priority(i: int) -> tuple[int, int, int]:
                neighbors = clauses[max(0, i - 1): i] + clauses[i + 1:i + 2]
                return sum(clause.relevance for clause in neighbors), -i, -clauses[i].weight

            primary = sorted((i for i in candidates if i not in right_adjacent), key=priority)
            secondary = sorted((i for i in candidates if i in right_adjacent), key=priority)
            ordered_candidates = primary + secondary
            total_weight = sum(clause.weight for clause in clauses)
            removable_weight = sum(clauses[i].weight for i in removed)
            needed = total_weight * (original - target) / original
            keep_one = not any(
                not clause.removable and clause.text.strip() for clause in clauses
            )
            available = len(ordered_candidates) - int(keep_one)
            for i in ordered_candidates[:available]:
                if removable_weight >= needed:
                    break
                removed.add(i)
                removable_weight += clauses[i].weight

        keep_by_line: list[list[str]] = [[] for _ in lines]
        for index, clause in enumerate(clauses):
            if index not in removed:
                keep_by_line[clause.line].append(clause.text)
        compressed = "".join("".join(parts) + end for parts, (_, end) in zip(keep_by_line, lines))
        return FusionResult(
            compressed, original, count_tokens(compressed) if compressed != ctx.content else original,
            skipped=compressed == ctx.content,
        )
