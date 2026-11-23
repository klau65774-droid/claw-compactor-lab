"""Bilingual paragraph deduplication with bounded MinHash candidate buckets."""

from __future__ import annotations

import heapq
from zlib import crc32

from ..fusion.base import FusionContext, FusionResult, FusionStage
from ._text import FACT, count_tokens, layout_mask, query_terms, shingles, split_lines

_SIGNATURE_SIZE = 8
_BUCKET_SIZE = 4
_CANDIDATE_LIMIT = 4


class BilingualLshDedupStage(FusionStage):
    name = "bilingual_lsh_dedup"
    order = 20

    def __init__(self, threshold: float = 0.80) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    def should_apply(self, ctx: FusionContext) -> bool:
        return (
            ctx.content_type in {"text", "log", "search"}
            and len(ctx.content) >= 80
            and ("\n" in ctx.content or "\r" in ctx.content)
        )

    def apply(self, ctx: FusionContext, *, original_tokens: int | None = None) -> FusionResult:
        compressed, removed = self.deduplicate(ctx.content, ctx.query)
        original = count_tokens(ctx.content) if original_tokens is None else original_tokens
        if not removed:
            return FusionResult(ctx.content, original, original, skipped=True)
        return FusionResult(
            compressed, original, count_tokens(compressed),
            markers=[f"bilingual_lsh_dedup:{removed}_blocks_removed"],
        )

    def deduplicate(self, text: str, hint: str | None = None) -> tuple[str, int]:
        lines = split_lines(text)
        layout = layout_mask([line for line, _ in lines])
        query = query_terms(hint)
        exact: set[str] = set()
        retained: list[tuple[str, set[str]]] = []
        verification: dict[int, set[str]] = {}
        fact_signatures: dict[int, tuple[str, ...]] = {}
        buckets: dict[int, list[int]] = {}
        output: list[str] = []
        separator: list[str] = []
        removed = 0
        start = 0

        while start < len(lines):
            line, end = lines[start]
            if not line.strip():
                separator.append(line + end)
                start += 1
                continue
            stop = start + 1
            while stop < len(lines) and lines[stop][0].strip():
                stop += 1
            block = "".join(line + end for line, end in lines[start:stop])
            duplicate = False
            if len(block) >= 40 and not any(layout[start:stop]):
                normalized = block.rstrip("\r\n")
                if normalized in exact:
                    duplicate = True
                else:
                    exact.add(normalized)
                    words = shingles(normalized)
                    if len(words) >= 8:
                        # Bottom-k MinHash: a shared minimum shingle votes for a
                        # candidate; every removal still requires exact Jaccard.
                        signature = heapq.nsmallest(
                            _SIGNATURE_SIZE, {crc32(word.encode("utf-8")) for word in words},
                        )
                        votes: dict[int, int] = {}
                        for key in signature:
                            for index in buckets.get(key, ()):
                                votes[index] = votes.get(index, 0) + 1
                        detail: set[str] | None = None
                        facts: tuple[str, ...] | None = None
                        for index in heapq.nlargest(_CANDIDATE_LIMIT, votes, key=votes.__getitem__):
                            previous_text, previous = retained[index]
                            if not self._similar(words, previous) or (words & query) != (previous & query):
                                continue
                            if detail is None:
                                detail = shingles(normalized, word_bigrams=True)
                            if index not in verification:
                                verification[index] = shingles(previous_text, word_bigrams=True)
                            if not self._similar(detail, verification[index]):
                                continue
                            if facts is None:
                                facts = self._facts(normalized)
                            if index not in fact_signatures:
                                fact_signatures[index] = self._facts(previous_text)
                            if facts == fact_signatures[index]:
                                duplicate = True
                                break
                        if not duplicate:
                            index = len(retained)
                            retained.append((normalized, words))
                            if detail is not None:
                                verification[index] = detail
                            if facts is not None:
                                fact_signatures[index] = facts
                            for key in signature:
                                bucket = buckets.setdefault(key, [])
                                if len(bucket) >= _BUCKET_SIZE:
                                    del bucket[0]
                                bucket.append(index)

            if duplicate:
                removed += 1
            else:
                output.extend(separator)
                output.append(block)
            separator.clear()
            start = stop

        if not removed:
            return text, 0
        output.extend(separator)
        return "".join(output), removed

    def _similar(self, words: set[str], previous: set[str]) -> bool:
        overlap = len(words & previous)
        return overlap >= self.threshold * (len(words) + len(previous) - overlap)

    @staticmethod
    def _facts(text: str) -> tuple[str, ...]:
        return tuple(fact.lower() for fact in FACT.findall(text))
