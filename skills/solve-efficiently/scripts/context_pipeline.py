#!/usr/bin/env python3
"""Typed, budgeted context assembly with auditable selection receipts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence


WORD_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]*")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _name(value: object) -> str:
    return value.__class__.__name__


@dataclass(frozen=True)
class ContextItem:
    """One independently attributable unit of candidate context."""

    item_id: str
    source: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("item_id must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")


@dataclass(frozen=True)
class ContextBudget:
    max_chars: int
    max_items: int

    def __post_init__(self) -> None:
        if self.max_chars < 1:
            raise ValueError("max_chars must be positive")
        if self.max_items < 1:
            raise ValueError("max_items must be positive")


@dataclass
class ContextDecision:
    item_id: str
    source: str
    status: str
    reason: str
    original_chars: int
    selected_chars: int
    content_sha256: str
    consumed: bool = False
    usefulness: str = "unknown"
    stale: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"included", "excluded", "truncated"}:
            raise ValueError(f"unsupported context decision status: {self.status}")
        if self.usefulness not in {"unknown", "useful", "unused"}:
            raise ValueError(f"unsupported context usefulness: {self.usefulness}")


@dataclass
class ContextReceipt:
    schema_version: int
    query: str
    provider: str
    processors: list[str]
    selector: str
    budget: dict[str, int]
    selected_chars: int
    selected_items: int
    decisions: list[ContextDecision]
    end_to_end_improvement_proven: bool = False

    def mark_consumed(self, item_ids: Iterable[str]) -> None:
        consumed = set(item_ids)
        known = {decision.item_id for decision in self.decisions}
        unknown = consumed - known
        if unknown:
            raise ValueError(f"cannot consume unknown context items: {sorted(unknown)}")
        selected = {
            decision.item_id
            for decision in self.decisions
            if decision.status in {"included", "truncated"}
        }
        unselected = consumed - selected
        if unselected:
            raise ValueError(
                f"cannot consume unselected context items: {sorted(unselected)}"
            )
        for decision in self.decisions:
            if decision.item_id in consumed:
                decision.consumed = True

    def mark_usefulness(self, item_ids: Iterable[str], usefulness: str) -> None:
        if usefulness not in {"useful", "unused"}:
            raise ValueError("usefulness must be useful or unused")
        selected = {
            decision.item_id
            for decision in self.decisions
            if decision.status in {"included", "truncated"}
        }
        requested = set(item_ids)
        unknown = requested - selected
        if unknown:
            raise ValueError(
                f"cannot classify unselected context items: {sorted(unknown)}"
            )
        by_id = {decision.item_id: decision for decision in self.decisions}
        if usefulness == "useful":
            unconsumed = sorted(
                item_id for item_id in requested if not by_id[item_id].consumed
            )
            if unconsumed:
                raise ValueError(f"useful context must be consumed first: {unconsumed}")
        for item_id in requested:
            by_id[item_id].usefulness = usefulness

    def usage_metrics(self) -> dict[str, int]:
        selected = [
            decision
            for decision in self.decisions
            if decision.status in {"included", "truncated"}
        ]
        consumed = [decision for decision in selected if decision.consumed]
        useful = [decision for decision in selected if decision.usefulness == "useful"]
        explicit_unused = [
            decision for decision in selected if decision.usefulness == "unused"
        ]
        selected_chars = self.selected_chars
        consumed_chars = (
            sum(decision.selected_chars for decision in consumed)
            + max(0, len(consumed) - 1) * 2
        )
        useful_chars = (
            sum(decision.selected_chars for decision in useful)
            + max(0, len(useful) - 1) * 2
        )
        return {
            "candidate_items": len(self.decisions),
            "selected_items": len(selected),
            "consumed_items": len(consumed),
            "useful_items": len(useful),
            "explicit_unused_items": len(explicit_unused),
            "selected_unconsumed_items": sum(
                1 for decision in selected if not decision.consumed
            ),
            "stale_items": sum(1 for decision in self.decisions if decision.stale),
            "selected_chars": selected_chars,
            "consumed_chars": consumed_chars,
            "useful_chars": useful_chars,
            "estimated_selected_tokens": (selected_chars + 3) // 4,
        }

    def to_dict(self, *, include_usage: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if include_usage:
            payload["schema_version"] = 2
            payload["usage_metrics"] = self.usage_metrics()
        else:
            for decision in payload["decisions"]:
                decision.pop("usefulness", None)
                decision.pop("stale", None)
        return payload

    def to_json(self, *, include_usage: bool = False) -> str:
        return json.dumps(
            self.to_dict(include_usage=include_usage),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass(frozen=True)
class ContextSelection:
    items: tuple[ContextItem, ...]
    decisions: tuple[ContextDecision, ...]


@dataclass(frozen=True)
class PipelineResult:
    items: tuple[ContextItem, ...]
    receipt: ContextReceipt

    @property
    def content(self) -> str:
        return "\n\n".join(item.content for item in self.items)


@dataclass(frozen=True)
class ContextLintIssue:
    code: str
    item_ids: tuple[str, ...]
    message: str


class ContextProvider(Protocol):
    def provide(self, query: str) -> Sequence[ContextItem]: ...


class ContextProcessor(Protocol):
    def process(
        self, items: Sequence[ContextItem], query: str
    ) -> Sequence[ContextItem]: ...


class ContextSelector(Protocol):
    def select(
        self,
        items: Sequence[ContextItem],
        query: str,
        budget: ContextBudget,
    ) -> ContextSelection: ...


@dataclass(frozen=True)
class StaticContextProvider:
    items: Sequence[ContextItem]

    def provide(self, query: str) -> Sequence[ContextItem]:
        del query
        return tuple(self.items)


class NormalizeWhitespaceProcessor:
    """Normalize line endings and remove blank candidates without summarizing them."""

    def process(
        self, items: Sequence[ContextItem], query: str
    ) -> Sequence[ContextItem]:
        del query
        normalized: list[ContextItem] = []
        for item in items:
            content = item.content.replace("\r\n", "\n").replace("\r", "\n").strip()
            if not content:
                continue
            normalized.append(
                ContextItem(item.item_id, item.source, content, dict(item.metadata))
            )
        return normalized


def _query_score(item: ContextItem, query_terms: set[str]) -> tuple[int, int]:
    haystack = f"{item.source} {item.content}".lower()
    matches = sum(haystack.count(term) for term in query_terms)
    priority = item.metadata.get("priority", 0)
    if not isinstance(priority, int):
        priority = 0
    return matches, priority


class RankedBudgetSelector:
    """Rank candidates lexically, then fill a strict character/item budget."""

    def select(
        self,
        items: Sequence[ContextItem],
        query: str,
        budget: ContextBudget,
    ) -> ContextSelection:
        query_terms = {token.lower() for token in WORD_PATTERN.findall(query)}
        indexed = list(enumerate(items))
        indexed.sort(
            key=lambda pair: (
                -_query_score(pair[1], query_terms)[0],
                -_query_score(pair[1], query_terms)[1],
                pair[0],
            )
        )
        remaining_chars = budget.max_chars
        selected: list[ContextItem] = []
        decisions_by_id: dict[str, ContextDecision] = {}
        for _, item in indexed:
            original_chars = len(item.content)
            base = {
                "item_id": item.item_id,
                "source": item.source,
                "original_chars": original_chars,
                "content_sha256": _content_hash(item.content),
            }
            if len(selected) >= budget.max_items:
                decisions_by_id[item.item_id] = ContextDecision(
                    **base, status="excluded", reason="item_budget", selected_chars=0
                )
                continue
            if remaining_chars <= 0:
                decisions_by_id[item.item_id] = ContextDecision(
                    **base,
                    status="excluded",
                    reason="character_budget",
                    selected_chars=0,
                )
                continue
            separator_chars = 2 if selected else 0
            available_chars = remaining_chars - separator_chars
            if available_chars <= 0:
                decisions_by_id[item.item_id] = ContextDecision(
                    **base,
                    status="excluded",
                    reason="character_budget",
                    selected_chars=0,
                )
                continue
            if original_chars <= available_chars:
                selected.append(item)
                remaining_chars -= separator_chars + original_chars
                decisions_by_id[item.item_id] = ContextDecision(
                    **base,
                    status="included",
                    reason="ranked_within_budget",
                    selected_chars=original_chars,
                )
                continue
            truncated_content = item.content[:available_chars]
            selected.append(
                ContextItem(
                    item.item_id,
                    item.source,
                    truncated_content,
                    {**dict(item.metadata), "truncated": True},
                )
            )
            decisions_by_id[item.item_id] = ContextDecision(
                **base,
                status="truncated",
                reason="character_budget",
                selected_chars=len(truncated_content),
            )
            remaining_chars = 0

        decisions = tuple(decisions_by_id[item.item_id] for item in items)
        return ContextSelection(tuple(selected), decisions)


class ContextPipeline:
    def __init__(
        self,
        provider: ContextProvider,
        processors: Sequence[ContextProcessor],
        selector: ContextSelector,
    ) -> None:
        self.provider = provider
        self.processors = tuple(processors)
        self.selector = selector

    def run(self, query: str, budget: ContextBudget) -> PipelineResult:
        if not query.strip():
            raise ValueError("query must not be empty")
        original = tuple(self.provider.provide(query))
        _require_unique_ids(original, "provider")
        invalid_expiry = sorted(
            item.item_id
            for item in original
            if "valid_until" in item.metadata
            and _parse_timestamp(item.metadata.get("valid_until")) is None
        )
        if invalid_expiry:
            raise ValueError(
                f"context items have invalid valid_until timestamps: {invalid_expiry}"
            )
        current_time = _utc_now()
        expired_ids = {
            item.item_id
            for item in original
            if (valid_until := _parse_timestamp(item.metadata.get("valid_until")))
            is not None
            and valid_until <= current_time
        }
        processed = tuple(item for item in original if item.item_id not in expired_ids)
        for processor in self.processors:
            processed = tuple(processor.process(processed, query))
            _require_unique_ids(processed, _name(processor))
            reintroduced_expired = {
                item.item_id for item in processed if item.item_id in expired_ids
            }
            if reintroduced_expired:
                raise ValueError(
                    "processors reintroduced expired context item ids: "
                    f"{sorted(reintroduced_expired)}"
                )

        original_by_id = {item.item_id: item for item in original}
        unknown = {item.item_id for item in processed} - set(original_by_id)
        if unknown:
            raise ValueError(
                f"processors introduced unknown item ids: {sorted(unknown)}"
            )
        selection = self.selector.select(processed, query, budget)
        if not isinstance(selection, ContextSelection):
            raise ValueError("selector must return ContextSelection")
        selected_ids = [item.item_id for item in selection.items]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selector returned duplicate item ids")
        processed_ids = {item.item_id for item in processed}
        if not set(selected_ids).issubset(processed_ids):
            raise ValueError("selector returned unknown item ids")

        decisions = list(selection.decisions)
        _validate_selection(processed, selection, budget)
        for item in original:
            if item.item_id not in processed_ids:
                decisions.append(
                    ContextDecision(
                        item_id=item.item_id,
                        source=item.source,
                        status="excluded",
                        reason=(
                            "expired"
                            if item.item_id in expired_ids
                            else "processor_filtered"
                        ),
                        original_chars=len(item.content),
                        selected_chars=0,
                        content_sha256=_content_hash(item.content),
                    )
                )
        decisions.sort(
            key=lambda decision: list(original_by_id).index(decision.item_id)
        )
        for decision in decisions:
            valid_until = _parse_timestamp(
                original_by_id[decision.item_id].metadata.get("valid_until")
            )
            decision.stale = valid_until is not None and valid_until <= current_time
        receipt = ContextReceipt(
            schema_version=1,
            query=query,
            provider=_name(self.provider),
            processors=[_name(processor) for processor in self.processors],
            selector=_name(self.selector),
            budget={"max_chars": budget.max_chars, "max_items": budget.max_items},
            selected_chars=sum(len(item.content) for item in selection.items)
            + max(0, len(selection.items) - 1) * 2,
            selected_items=len(selection.items),
            decisions=decisions,
        )
        return PipelineResult(selection.items, receipt)


def _require_unique_ids(items: Sequence[ContextItem], stage: str) -> None:
    ids = [item.item_id for item in items]
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        raise ValueError(f"{stage} returned duplicate item ids: {duplicates}")


def _validate_selection(
    processed: Sequence[ContextItem],
    selection: ContextSelection,
    budget: ContextBudget,
) -> None:
    """Reject selector output that cannot support a truthful receipt."""

    decision_ids = [decision.item_id for decision in selection.decisions]
    processed_by_id = {item.item_id: item for item in processed}
    if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != set(
        processed_by_id
    ):
        raise ValueError(
            "selector decisions must cover each processed item exactly once"
        )
    if len(selection.items) > budget.max_items:
        raise ValueError("selector selection exceeds item budget")
    rendered_chars = (
        sum(len(item.content) for item in selection.items)
        + max(0, len(selection.items) - 1) * 2
    )
    if rendered_chars > budget.max_chars:
        raise ValueError("selector selection exceeds character budget")

    selected_by_id = {item.item_id: item for item in selection.items}
    for decision in selection.decisions:
        original = processed_by_id[decision.item_id]
        selected = selected_by_id.get(decision.item_id)
        common_valid = (
            decision.source == original.source
            and decision.original_chars == len(original.content)
            and decision.content_sha256 == _content_hash(original.content)
            and 0 <= decision.selected_chars <= decision.original_chars
            and decision.consumed is False
            and decision.usefulness == "unknown"
            and decision.stale is False
        )
        if not common_valid:
            raise ValueError("selector decisions do not match processed context")
        if selected is None:
            if decision.status != "excluded" or decision.selected_chars != 0:
                raise ValueError("selector decisions do not match selection")
            continue
        if selected.source != original.source or decision.selected_chars != len(
            selected.content
        ):
            raise ValueError("selector decisions do not match selection")
        if decision.status == "included":
            valid_content = selected.content == original.content
        elif decision.status == "truncated":
            valid_content = len(selected.content) < len(
                original.content
            ) and original.content.startswith(selected.content)
        else:
            valid_content = False
        if not valid_content:
            raise ValueError("selector decisions do not match selection")


def lint_context(
    items: Sequence[ContextItem],
    receipt: ContextReceipt | None = None,
    *,
    now: datetime | None = None,
) -> list[ContextLintIssue]:
    """Find deterministic context defects without asserting model quality."""

    issues: list[ContextLintIssue] = []
    by_hash: dict[str, list[str]] = {}
    facts: dict[str, dict[str, list[str]]] = {}
    current_time = (now or _utc_now()).astimezone(timezone.utc)
    for item in items:
        normalized = " ".join(item.content.split()).casefold()
        by_hash.setdefault(_content_hash(normalized), []).append(item.item_id)
        fact_key = item.metadata.get("fact_key")
        fact_value = item.metadata.get("fact_value")
        if isinstance(fact_key, str) and fact_key and fact_value is not None:
            facts.setdefault(fact_key, {}).setdefault(str(fact_value), []).append(
                item.item_id
            )
        raw_valid_until = item.metadata.get("valid_until")
        valid_until = _parse_timestamp(raw_valid_until)
        if "valid_until" in item.metadata and valid_until is None:
            issues.append(
                ContextLintIssue(
                    "invalid-expiry",
                    (item.item_id,),
                    f"{item.item_id} has an invalid valid_until timestamp",
                )
            )
        if valid_until is not None and valid_until <= current_time:
            issues.append(
                ContextLintIssue(
                    "stale",
                    (item.item_id,),
                    f"{item.item_id} expired at {valid_until.isoformat()}",
                )
            )

    for item_ids in by_hash.values():
        if len(item_ids) > 1:
            issues.append(
                ContextLintIssue(
                    "duplicate",
                    tuple(item_ids),
                    "normalized content is duplicated",
                )
            )
    for fact_key, values in facts.items():
        if len(values) > 1:
            item_ids = tuple(item_id for ids in values.values() for item_id in ids)
            issues.append(
                ContextLintIssue(
                    "contradiction",
                    item_ids,
                    f"fact {fact_key!r} has conflicting values: {sorted(values)}",
                )
            )
    if receipt is not None:
        for decision in receipt.decisions:
            if decision.status in {"included", "truncated"} and not decision.consumed:
                issues.append(
                    ContextLintIssue(
                        "unconsumed",
                        (decision.item_id,),
                        "selected context was not marked consumed",
                    )
                )
    return sorted(issues, key=lambda issue: (issue.code, issue.item_ids))
