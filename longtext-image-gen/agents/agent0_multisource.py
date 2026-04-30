"""
agents/agent0_multisource.py — Agent 0 多信源聚合（v3.2）

职责：
- 标准化 single/multi 输入为 SourceBundle
- 多信源时进行轻量语义去重与冲突初筛
- 保留 source_id，不用 LLM 补全缺失背景
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from infra.tracing import trace
from ir.models import (
    Disagreement,
    DuplicateCluster,
    Source,
    SourceAuthority,
    SourceBundle,
    SourceMode,
)

_AUTHORITY_RANK = {
    SourceAuthority.AUTHORITATIVE: 5,
    SourceAuthority.MAINSTREAM_MEDIA: 4,
    SourceAuthority.PROFESSIONAL_BLOG: 3,
    SourceAuthority.GENERAL_WEB: 2,
    SourceAuthority.USER_GENERATED: 1,
}
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|亿|万|万元|亿元|人|次|个)?")


def _normalize_source(source: Source, fallback_index: int) -> Source:
    source_id = source.source_id or f"s{fallback_index}"
    source_name = source.source_name or source.title or source.url or source_id
    return source.model_copy(update={"source_id": source_id, "source_name": source_name})


def _source_similarity(left: Source, right: Source) -> float:
    left_sample = left.raw_text[:600]
    right_sample = right.raw_text[:600]
    if not left_sample or not right_sample:
        return 0.0
    return SequenceMatcher(None, left_sample, right_sample).ratio()


def _build_duplicate_clusters(sources: list[Source], threshold: float = 0.85) -> list[DuplicateCluster]:
    clusters: list[DuplicateCluster] = []
    used: set[str] = set()
    for idx, source in enumerate(sources):
        if source.source_id in used:
            continue
        members = [source]
        for other in sources[idx + 1:]:
            if other.source_id in used:
                continue
            if _source_similarity(source, other) >= threshold:
                members.append(other)
        if len(members) > 1:
            representative = max(members, key=lambda s: _AUTHORITY_RANK.get(s.authority, 0))
            member_ids = [s.source_id for s in members]
            used.update(member_ids)
            clusters.append(DuplicateCluster(
                cluster_id=f"dup_{len(clusters) + 1}",
                member_source_ids=member_ids,
                representative_source_id=representative.source_id,
                reason="source_text_similarity>=0.85",
            ))
    return clusters


def _extract_numbers(text: str) -> set[str]:
    return {m.group(0).replace(" ", "") for m in _NUMBER_RE.finditer(text or "") if len(m.group(0).strip()) >= 2}


def _build_disagreements(sources: list[Source]) -> list[Disagreement]:
    disagreements: list[Disagreement] = []
    number_sets = [(source, _extract_numbers(source.raw_text)) for source in sources]
    for idx, (source, numbers) in enumerate(number_sets):
        for other, other_numbers in number_sets[idx + 1:]:
            if not numbers or not other_numbers:
                continue
            if numbers.isdisjoint(other_numbers):
                disagreements.append(Disagreement(
                    disagreement_id=f"dis_{len(disagreements) + 1}",
                    disagreement_type="data_disagreement",
                    source_views=[
                        {"source_id": source.source_id, "claim_text": ", ".join(sorted(numbers)[:5])},
                        {"source_id": other.source_id, "claim_text": ", ".join(sorted(other_numbers)[:5])},
                    ],
                    resolution="preserve_disagreement",
                ))
                break
        if len(disagreements) >= 5:
            break
    return disagreements


def _join_sources(sources: Iterable[Source]) -> str:
    chunks = []
    for source in sources:
        title = source.title or source.source_name or source.source_id
        chunks.append(f"[来源 {source.source_id}] {title}\n{source.raw_text}")
    return "\n\n".join(chunks)


@trace("Agent0.MultiSource")
def run_agent0_multisource(source_bundle: SourceBundle) -> SourceBundle:
    """运行 Agent 0，多信源时补充去重/冲突/元信息，单源时保持兼容。"""
    sources = [_normalize_source(source, idx + 1) for idx, source in enumerate(source_bundle.sources)]
    mode = SourceMode.MULTI if len(sources) > 1 or source_bundle.mode == SourceMode.MULTI else SourceMode.SINGLE

    if mode == SourceMode.SINGLE:
        text = source_bundle.text or (sources[0].raw_text if sources else "")
        return source_bundle.model_copy(update={
            "mode": SourceMode.SINGLE,
            "text": text,
            "sources": sources,
            "char_count": len(text),
            "multi_source_meta": {},
        })

    duplicate_clusters = _build_duplicate_clusters(sources)
    disagreements = _build_disagreements(sources)
    text = _join_sources(sources)
    meta = {
        "source_count": len(sources),
        "duplicate_cluster_count": len(duplicate_clusters),
        "disagreement_count": len(disagreements),
        "source_ids": [source.source_id for source in sources],
    }
    print(
        f"[Agent0] 多信源聚合: {len(sources)} sources, "
        f"{len(duplicate_clusters)} duplicate clusters, {len(disagreements)} disagreements"
    )
    return source_bundle.model_copy(update={
        "mode": SourceMode.MULTI,
        "text": text,
        "sources": sources,
        "duplicate_clusters": duplicate_clusters,
        "disagreements": disagreements,
        "multi_source_meta": meta,
        "char_count": sum(len(source.raw_text) for source in sources),
    })
