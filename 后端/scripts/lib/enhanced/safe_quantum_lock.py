"""Lossless dynamic-value indexing and inline credential redaction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..fusion.base import FusionContext, FusionResult, FusionStage
from ._text import count_tokens

_SECRET_RULES = {
    "openai_api_key": r"(?:sk|rk)-[a-zA-Z0-9_-]{12,}|(?:pk_live|pk_test)_[a-zA-Z0-9_-]{16,}",
    "aliyun_access_key": r"LTAI[A-Za-z0-9]{12,}",
    "aws_access_key": r"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "github_pat": r"(?:ghp|gho|github_pat)_[A-Za-z0-9_]{16,}",
    "jwt_token": r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    "cn_mobile": r"(?:\+86[ -]?)?1[3-9][0-9]{9}",
}
_DYNAMIC_RULES = {
    "date": r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?",
    "time": r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]",
    "uuid": r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}",
    "timestamp": r"(?:1[5-9][0-9]{8}|[2-9][0-9]{9}|[0-9]{13})",
    "id": r"[0-9A-Fa-f]{32,64}",
}


def _compile(rules: dict[str, str]) -> re.Pattern[str]:
    # ASCII boundaries allow credentials adjacent to CJK prose and exclude
    # phone-like substrings of long numeric order IDs and hex digests.
    alternatives = "|".join(f"(?P<{kind}>{expression})" for kind, expression in rules.items())
    return re.compile(r"(?<![A-Za-z0-9_])(?:" + alternatives + r")(?![A-Za-z0-9_])")


_SECRETS = _compile(_SECRET_RULES)
_FRAGMENTS = _compile({**_SECRET_RULES, **_DYNAMIC_RULES})
_PLACEHOLDER = re.compile(r"<(?:secret|date|time|uuid|timestamp|id)(?:_[0-9]+)?>")


@dataclass(frozen=True)
class Finding:
    kind: str
    placeholder: str
    masked_preview: str
    count: int


@dataclass(frozen=True)
class Stabilized:
    prefix: str
    appendix: str
    findings: tuple[Finding, ...]
    indexed: bool

    @property
    def text(self) -> str:
        return self.prefix + self.appendix


def count_secret_occurrences(text: str) -> int:
    return sum(1 for _ in _SECRETS.finditer(text))


def _preview(value: str) -> str:
    return f"{value[:4]}***{value[-4:]} [REDACTED]"


def _replace(
    text: str,
    *,
    dynamics: bool,
    redact_secrets: bool,
    index_placeholders: bool,
    include_appendix: bool,
) -> Stabilized:
    pattern = _FRAGMENTS if dynamics else _SECRETS
    occupied = set(_PLACEHOLDER.findall(text)) if "<" in text else set()
    values: dict[tuple[str, str], str] = {}
    occurrences: dict[tuple[str, str], int] = {}
    serials: dict[str, int] = {}
    appendix: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        kind = match.lastgroup
        assert kind is not None
        value = match[0]
        secret = kind in _SECRET_RULES
        slot = "secret" if secret else kind
        key = (kind, value)
        if key not in values:
            if index_placeholders:
                number = serials.get(slot, 0) + 1
                while f"<{slot}_{number}>" in occupied:
                    number += 1
                serials[slot] = number
                placeholder = f"<{slot}_{number}>"
            else:
                placeholder = f"<{slot}>"
            values[key] = placeholder
            if include_appendix:
                display = _preview(value) if secret and redact_secrets else value
                appendix.append(f"{placeholder}: {display}")
        occurrences[key] = occurrences.get(key, 0) + 1
        if secret and not redact_secrets and not include_appendix:
            return value
        return values[key]

    prefix = pattern.sub(substitute, text)
    findings = tuple(
        Finding(kind, values[kind, value], _preview(value), count)
        for (kind, value), count in occurrences.items()
        if kind in _SECRET_RULES
    )
    suffix = "\n<dynamic-context>\n" + "\n".join(appendix) + "\n</dynamic-context>" if appendix else ""
    return Stabilized(prefix, suffix, findings, bool(values) and index_placeholders)


def redact_inline(text: str) -> str:
    """Replace supported credentials without appending their values."""
    return _replace(
        text, dynamics=False, redact_secrets=True, index_placeholders=True, include_appendix=False,
    ).prefix


class SafeQuantumLockStage(FusionStage):
    name = "safe_quantum_lock"
    order = 50

    def __init__(self, *, redact_secrets: bool = True, index_placeholders: bool = True) -> None:
        self.redact_secrets = redact_secrets
        self.index_placeholders = index_placeholders

    def stabilize(self, text: str) -> Stabilized:
        return _replace(
            text,
            dynamics=True,
            redact_secrets=self.redact_secrets,
            index_placeholders=self.index_placeholders,
            include_appendix=True,
        )

    def should_apply(self, ctx: FusionContext) -> bool:
        return ctx.role == "system" and ctx.content_type == "text" and bool(_FRAGMENTS.search(ctx.content))

    def apply(self, ctx: FusionContext) -> FusionResult:
        result = self.stabilize(ctx.content)
        return FusionResult(
            content=result.text,
            original_tokens=count_tokens(ctx.content),
            compressed_tokens=count_tokens(result.text),
            skipped=not result.appendix,
        )
