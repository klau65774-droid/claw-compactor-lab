"""Deterministic JSON array sampling and bounded rewind storage."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from threading import RLock

from ..fusion.base import FusionContext, FusionResult, FusionStage
from ..rewind.marker import embed_marker
from ..rewind.store import CacheEntry, RewindStore
from ._text import count_tokens, digest, query_terms, shingles

_ERROR = re.compile(
    r"\b(?:[A-Za-z_]*(?:error|exception)|fail(?:ed|ure|ing)?|fatal|timeout)\b"
    r"|失败|异常|超时|错误|熔断|告警|\b[45][0-9]{2}\b",
    re.IGNORECASE | re.ASCII,
)
_JSON_SYNTAX = re.compile(r'["\\{}\[\]]')


class BoundedRewindStore:
    """Thread-safe UTC-independent LRU with UTF-8 payload and entry budgets."""

    def __init__(
        self, max_entries: int = 500, max_bytes: int = 16 * 1024 * 1024, ttl_seconds: int = 600,
    ) -> None:
        if min(max_entries, max_bytes, ttl_seconds) <= 0:
            raise ValueError("rewind limits must be positive")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._store = RewindStore(max_entries=max_entries, ttl_seconds=ttl_seconds)
        self._sizes: dict[str, int] = {}
        self._bytes = 0
        self._lock = RLock()

    def _discard(self, hash_id: str) -> None:
        self._store._cache.pop(hash_id, None)
        self._bytes -= self._sizes.pop(hash_id)

    def _expire(self) -> None:
        cutoff = time.monotonic() - self._store.ttl_seconds
        expired = [key for key, entry in self._store._cache.items() if entry.stored_at < cutoff]
        for key in expired:
            self._discard(key)

    def store(
        self, original: str, compressed: str, original_tokens: int = 0, compressed_tokens: int = 0,
    ) -> str | None:
        size = len(original.encode("utf-8")) + len(compressed.encode("utf-8"))
        if size > self.max_bytes:
            return None
        hash_id = digest(original)[:24]
        with self._lock:
            self._expire()
            if hash_id in self._sizes:
                self._discard(hash_id)
            while len(self._sizes) >= self.max_entries or self._bytes + size > self.max_bytes:
                self._discard(next(iter(self._store._cache)))
            self._store.store(original, compressed, original_tokens, compressed_tokens)
            self._sizes[hash_id] = size
            self._bytes += size
        return hash_id

    def get(self, hash_id: str) -> CacheEntry | None:
        with self._lock:
            self._expire()
            entry = self._store._cache.get(hash_id)
            if entry is not None:
                self._store._cache.move_to_end(hash_id)
            return entry

    def retrieve(self, hash_id: str) -> str | None:
        entry = self.get(hash_id)
        return entry.original if entry is not None else None

    @property
    def size(self) -> int:
        with self._lock:
            self._expire()
            return len(self._sizes)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            self._expire()
            return self._bytes


def within_json_depth(text: str, max_depth: int = 32) -> bool:
    """Reject excessive nesting before calling the recursive standard decoder."""
    depth = 0
    in_string = False
    escaped_at = -1
    for match in _JSON_SYNTAX.finditer(text):
        if match.start() == escaped_at:
            continue
        char = match[0]
        if char == '"':
            in_string = not in_string
        elif in_string:
            if char == "\\":
                escaped_at = match.end()
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                return False
        elif char in "]}":
            depth -= 1
    return True


def _dict_parts(value: dict[object, object]) -> Iterator[object]:
    for key, item in value.items():
        yield key
        yield item


def contains_error(value: object, max_depth: int = 32) -> bool:
    """Keep a record if it has an error or exceeds the safe inspection budget."""
    stack = [iter((value,))]
    visited = 0
    while stack:
        item = next(stack[-1], stack)
        if item is stack:
            stack.pop()
            continue
        visited += 1
        if visited > 16_384:
            return True
        if isinstance(item, str) and _ERROR.search(item):
            return True
        if isinstance(item, (int, float)) and 400 <= item <= 599:
            return True
        if isinstance(item, (dict, list)):
            if len(stack) > max_depth:
                return True
            stack.append(_dict_parts(item) if isinstance(item, dict) else iter(item))
    return False


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class DeterministicIonizerStage(FusionStage):
    name = "deterministic_ionizer"
    order = 15

    def __init__(
        self, rewind_store: BoundedRewindStore | None = None, *,
        deterministic_seed: int = 0, sample_size: int | None = 20,
    ) -> None:
        self.rewind_store = rewind_store
        self.seed = deterministic_seed
        self.sample_size = sample_size

    def should_apply(self, ctx: FusionContext) -> bool:
        return ctx.content_type == "json"

    def apply(self, ctx: FusionContext, *, original_tokens: int | None = None) -> FusionResult:
        text = ctx.content
        original = count_tokens(text) if original_tokens is None else original_tokens
        unchanged = FusionResult(text, original, original, skipped=True)
        if not within_json_depth(text):
            return FusionResult(
                text, original, original, skipped=True, warnings=["JSON depth exceeds 32"],
            )
        try:
            parsed: object = json.loads(text)
            if not isinstance(parsed, (list, dict)):
                return unchanged
            compact = _compact_json(parsed)
            compact.encode("utf-8")
        except (ValueError, RecursionError):
            return unchanged
        if not isinstance(parsed, list) or self.sample_size is None or len(parsed) <= 6:
            return FusionResult(compact, original, count_tokens(compact), skipped=compact == text)

        items: list[object] = parsed
        boundaries = set(range(3)) | set(range(len(items) - 3, len(items)))
        query = query_terms(ctx.query)
        required = boundaries | {index for index, item in enumerate(items) if contains_error(item)}
        serialized = [_compact_json(item) for item in items]

        # Reserve one representative for each query term that occurs anywhere,
        # rather than forcing every row with a common term into the sample.
        uncovered = set(query)
        for index, item in enumerate(serialized):
            if uncovered:
                matches = uncovered & shingles(item, cjk_unigrams=False)
                if matches:
                    required.add(index)
                    uncovered -= matches

        unprotected = [index for index in range(len(items)) if index not in required]
        slots = max(0, self.sample_size - len(required))
        if ctx.token_budget is not None and ctx.token_budget < original:
            # Respect the budget where possible without displacing errors or
            # boundary records. The exact count, including the marker, follows.
            slots = min(slots, max(0, len(items) * ctx.token_budget // original - len(required)))

        # Split the middle into disjoint strides, then pick a stable hash
        # minimum in each stride to avoid clustering the selected records.
        selected: list[int] = []
        stride_count = min(slots, len(unprotected))
        for stratum in range(stride_count):
            start = stratum * len(unprotected) // stride_count
            stop = (stratum + 1) * len(unprotected) // stride_count
            selected.append(min(
                unprotected[start:stop], key=lambda i: digest(f"{self.seed}:{i}:{serialized[i]}"),
            ))

        def payload(extra: list[int]) -> tuple[str, int]:
            indices = sorted(required.union(extra))
            value = "[" + ",".join(serialized[i] for i in indices) + "]"
            if self.rewind_store is not None:
                value = embed_marker(value, len(items), len(indices), digest(text)[:24])
            return value, count_tokens(value)

        if len(required) + len(selected) < len(items):
            result, tokens = payload(selected)
            if ctx.token_budget is not None and tokens > ctx.token_budget and selected:
                result, tokens = payload([])
            # Store only a candidate eligible for the monotonic gate.
            if tokens <= original and tokens < count_tokens(compact):
                if self.rewind_store is None:
                    return FusionResult(result, original, tokens)
                key = self.rewind_store.store(text, result, original, tokens)
                if key is not None:
                    return FusionResult(result, original, tokens, markers=[key])
        return FusionResult(compact, original, count_tokens(compact), skipped=compact == text)
