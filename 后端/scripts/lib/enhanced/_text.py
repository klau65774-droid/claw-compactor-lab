"""Shared token accounting and bilingual text primitives."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from ..tokens import estimate_tokens, using_tiktoken

if using_tiktoken():
    import tiktoken

    _encoding: tiktoken.Encoding | None = tiktoken.get_encoding("cl100k_base")
else:
    _encoding = None

_WORD_OR_CJK = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")
_LINE_END = re.compile(r"(\r\n|\r|\n)")
_FENCE = re.compile(r"^\s*(?P<fence>\x60{3,}|~{3,})(?P<info>.*)$")
_LAYOUT = re.compile(r"^(?:#{1,6}(?:\s|$)|>\s?|(?:[-*+]|\d+\.)\s+|\||---\s*$|[-+]{3}\s|@@)")
FACT = re.compile(
    r"\b(?:not|never|no|from|to|in|is|without|cannot)\b|"
    r"禁止|不能|不得|不允许|不可|失败|异常|错误|超时|熔断|告警|"
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|"
    r"\b(?:19|20)\d{2}\b|"
    r"(?<![0-9])\d+(?:\.\d+)?\s*"
    r"(?:%|ms\b|s\b|秒|毫秒|tokens?\b|倍|亿元|万元|万|亿|元|年|月|日|家|台|个|次|件|吨|km\b|MB\b|GB\b|KB\b)",
    re.IGNORECASE | re.ASCII,
)


def count_tokens(text: str) -> int:
    # Treat special-token spellings as literal text and avoid caching raw input.
    if _encoding is None:
        return estimate_tokens(text)
    return len(_encoding.encode_ordinary(text))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shingles(text: str, *, cjk_unigrams: bool = True, word_bigrams: bool = False) -> set[str]:
    if text.isascii():
        words = _WORD_OR_CJK.findall(text.lower())
        ascii_terms = set(words)
        if word_bigrams:
            ascii_terms.update(map(" ".join, zip(words, words[1:])))
        return ascii_terms
    result: set[str] = set()
    previous = ""
    for match in _WORD_OR_CJK.finditer(text.lower()):
        term = match[0]
        if term.isascii():
            result.add(term)
            if previous and word_bigrams:
                result.add(f"{previous} {term}")
            previous = term
        else:
            if cjk_unigrams or len(term) == 1:
                result.update(term)
            result.update(a + b for a, b in zip(term, term[1:]))
            previous = ""
    return result


def query_terms(query: str | None) -> set[str]:
    return shingles(query or "", cjk_unigrams=False)


def needle_retention(original: str, compressed: str, query: str | None) -> float:
    terms = query_terms(query)
    if not terms:
        return 100.0
    present = terms & shingles(original, cjk_unigrams=False)
    if not present:
        return 100.0
    kept = present & shingles(compressed, cjk_unigrams=False)
    return round(100 * len(kept) / len(present), 2)


def split_lines(text: str) -> list[tuple[str, str]]:
    """Separate line bodies and exact terminators, including CRLF."""
    parts = _LINE_END.split(text)
    return list(zip(parts[::2], [*parts[1::2], ""]))


def layout_mask(lines: Sequence[str]) -> list[bool]:
    """Identify Markdown, fenced code, and the body of unified diff hunks."""
    protected: list[bool] = []
    fence = ""
    in_hunk = False
    for line in lines:
        stripped = line.lstrip()
        marker = _FENCE.match(line)
        if fence:
            protected.append(True)
            if marker and marker["fence"].startswith(fence) and not marker["info"].strip():
                fence = ""
            continue
        if marker:
            fence = marker["fence"]
        if stripped.startswith("@@"):
            in_hunk = True
        elif in_hunk and line and not line.startswith((" ", "+", "-", "\\")):
            in_hunk = False
        protected.append(
            bool(marker or in_hunk or _LAYOUT.match(stripped) or line.startswith(("    ", "\t")))
            or stripped.startswith(("diff --git ", "index "))
            or stripped.count("|") >= 2
        )
    return protected


_COMMENT_DIRECTIVE = re.compile(
    r"^(?:#!|#\s*(?:pragma|region|endregion|if|elif|else|endif|define|undef|include|noqa|type:)|"
    r"//\s*(?:go:|eslint|@ts-|pragma|region|endregion)|///<)",
    re.IGNORECASE,
)


def is_strippable_code_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    if _COMMENT_DIRECTIVE.match(stripped):
        return False
    return stripped.startswith(("//", "#"))


def structure_preserved(original: str, compressed: str, content_type: str) -> bool:
    original_lines = original.splitlines()
    if content_type == "diff":
        required = original_lines
    elif content_type == "code":
        tail_start = max(0, len(original_lines) - 25)
        required = [
            line for idx, line in enumerate(original_lines)
            if (idx >= tail_start and line.strip())
            or (line.strip() and not is_strippable_code_comment_line(line) and not line.lstrip().startswith(("/*", "*")))
        ]
    else:
        required = [line for line, keep in zip(original_lines, layout_mask(original_lines)) if keep]
    if not required:
        return True
    remaining = iter(compressed.splitlines())
    return all(any(candidate == line for candidate in remaining) for line in required)
