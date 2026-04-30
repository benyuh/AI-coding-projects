"""
agents/agent2_understand.py — Agent 2 内容理解（v3.1.6）

v3.1.6 改动：
- 删除 text[:6000] + text[-2000:] 的超长截断路径
- 真分块：每个 chunk 带 char_start/char_end，独立逐块 LLM 调用
- 每个 Claim 写入 source_chunk_ids
- 每个 FactElement 写入 source_offset（chunk_id + char 偏移）
- 生成 ContextIndex 并赋值到 ContentTree.context_index
- 卡片预算 ≤ 15 张上限
"""

from __future__ import annotations

import re
from typing import Optional

from infra.tracing import trace
from ir.models import (
    Claim, ChunkRef, ContentTree, ContextIndex, FactElement,
    RouterDecision, SourceBundle, SourceMode, SourceOffset, UserType,
)
from llm.client import call_llm

# ── 分块阈值 ─────────────────────────────────────────────────────────────────
_CHUNK_SMALL = 3_000    # <= 3000 字：不分块
_CHUNK_MEDIUM = 10_000  # 3001-10000 字：按标题/2000字分块
# > 10000 字：按 3000 字硬分块

_SYSTEM_PROMPT = """你是一位内容分析专家。将文章拆解为核心声明（Claim）列表，并规划信息图卡片槽位。

输出严格 JSON（不含 Markdown 包裹）：
{
  "claims": [
    {
      "claim_text": "核心声明（≤50字）",
      "evidence_span": "原文中支持该声明的关键片段（≤100字，必须来自原文）",
      "fact_elements": [
        {
          "text": "原文中的精确数字/时间/实体",
          "element_type": "number|date|entity|percentage",
          "context_50": "前后25字上下文（含完整数字，不截断）"
        }
      ],
      "importance": 0.8
    }
  ],
  "card_budget": 8,
  "card_slots": [
    {"slot_index": 0, "suggested_type": "cover", "content_hint": "封面摘要"},
    {"slot_index": 1, "suggested_type": "data", "content_hint": "核心数据点"}
  ],
  "outline": "文章结构简述（≤80字）",
  "chunk_summary": "本段核心内容（≤50字）"
}

## 卡片预算规则（PRD 4.3.2）
- 字数 < 1000：3-5 张
- 字数 1000-3000：5-8 张
- 字数 3000-8000：8-12 张
- 字数 > 8000：12-15 张
- 专业用户 +2 张（信息密度更高）
- card_slots 第1张必须是 cover，最后1张必须是 summary

## 关键规则
- fact_elements 中的 text 必须是原文的精确字符串，不要截断、不要改写
- 数字如 758631、75.8万 必须完整抄录，不能写成 75万863 或其他拼接形式
- evidence_span 必须来自本段原文，≤100 字
"""

_USER_PROMPT_TEMPLATE = """分析以下文章片段（共 {total_chars} 字，当前段落 {chunk_chars} 字，用户类型：{user_type}），输出内容理解 JSON：

---
{text}
---

直接输出 JSON，不要解释。"""

_MERGE_PROMPT = """你是内容分析专家。将多个段落的分析结果合并为完整的信息图规划。

## 各段落分析摘要
{chunk_summaries}

## 全文字数
{total_chars}字，用户类型：{user_type}

## 合并规则
1. claims 合并去重，优先保留 importance 高的
2. card_budget 按全文字数重新计算（不要简单累加）
3. card_slots 重新规划，第1张 cover，最后1张 summary
4. outline 整合成全文结构描述

输出 JSON（格式同分块分析）：
{{
  "claims": [...],
  "card_budget": <整数>,
  "card_slots": [...],
  "outline": "全文结构（≤100字）"
}}

直接输出 JSON，不要解释。"""


def _calculate_card_budget(
    char_count: int,
    user_type: UserType,
    source_bundle: SourceBundle | None = None,
) -> tuple[int, int]:
    """
    按 PRD 4.3.2 计算卡片预算范围。
    返回 (min_cards, max_cards)
    """
    if char_count < 1000:
        min_c, max_c = 3, 5
    elif char_count < 3000:
        min_c, max_c = 5, 8
    elif char_count < 8000:
        min_c, max_c = 8, 12
    else:
        min_c, max_c = 12, 15

    if user_type == UserType.PROFESSIONAL:
        max_c = min(max_c + 2, 15)

    if source_bundle and source_bundle.mode == SourceMode.MULTI:
        if source_bundle.disagreements:
            max_c = min(max_c + 1, 15)
        if len(source_bundle.sources) > 5:
            max_c = min(max_c + 1, 15)

    return min_c, max_c


def _split_text_chunks_with_offsets(text: str) -> list[ChunkRef]:
    """
    按 PRD 4.3.1 规则分块，返回带 char_start/char_end 的 ChunkRef 列表。
    """
    char_count = len(text)

    if char_count <= _CHUNK_SMALL:
        return [ChunkRef(
            chunk_id="c0",
            char_start=0,
            char_end=char_count,
            text=text,
            section_title=None,
        )]

    chunks: list[tuple[int, int, Optional[str]]] = []  # (start, end, title)

    if char_count <= _CHUNK_MEDIUM:
        # 按标题分块（# ## ###），不超过 2000 字/块
        sections = re.split(r'\n(?=#{1,3}\s)', text)
        pos = 0
        current_start = 0
        current_text = ""
        current_title: Optional[str] = None

        for section in sections:
            if len(current_text) + len(section) <= 2000:
                if not current_text:
                    # 尝试提取本段标题
                    title_match = re.match(r'^(#{1,3}\s+.+)', section)
                    if title_match:
                        current_title = title_match.group(1).strip('#').strip()
                current_text += section + "\n"
            else:
                if current_text:
                    end = current_start + len(current_text)
                    chunks.append((current_start, end, current_title))
                    current_start = end
                current_text = section + "\n"
                title_match = re.match(r'^(#{1,3}\s+.+)', section)
                current_title = title_match.group(1).strip('#').strip() if title_match else None

        if current_text:
            end = current_start + len(current_text)
            chunks.append((current_start, end, current_title))

    else:
        # > 10000 字：硬分块，每 3000 字
        step = 3000
        for i in range(0, char_count, step):
            chunks.append((i, min(i + step, char_count), None))

    result = []
    for idx, (start, end, title) in enumerate(chunks):
        result.append(ChunkRef(
            chunk_id=f"c{idx}",
            char_start=start,
            char_end=end,
            text=text[start:end],
            section_title=title,
        ))
    return result


def _find_fact_offset_in_chunk(fact_text: str, chunk: ChunkRef) -> SourceOffset:
    """在 chunk.text 中查找 fact_text 的位置，返回 SourceOffset。"""
    pos = chunk.text.find(fact_text)
    if pos >= 0:
        return SourceOffset(
            chunk_id=chunk.chunk_id,
            char_start=chunk.char_start + pos,
            char_end=chunk.char_start + pos + len(fact_text),
            text=fact_text,
        )
    return SourceOffset(
        chunk_id=chunk.chunk_id,
        char_start=-1,
        char_end=-1,
        text=fact_text,
    )


def _parse_claims_from_raw(
    raw_result: dict,
    source_bundle: SourceBundle,
    chunk_id: str,
    chunk: ChunkRef,
) -> list[Claim]:
    """解析 LLM 输出中的 claims 列表。"""
    claims = []
    default_source_id = (
        source_bundle.sources[0].source_id
        if source_bundle.sources
        else source_bundle.source_id
    )
    for c in raw_result.get("claims", []):
        fact_elements = []
        for fe in c.get("fact_elements", []):
            try:
                fe_text = str(fe.get("text", ""))
                source_offset = _find_fact_offset_in_chunk(fe_text, chunk) if fe_text else None
                fact_elements.append(FactElement(
                    text=fe_text,
                    element_type=str(fe.get("element_type", "entity")),
                    context_50=str(fe.get("context_50", "")),
                    source_offset=source_offset,
                ))
            except Exception:
                pass

        source_refs = c.get("source_refs") or [default_source_id]
        if isinstance(source_refs, str):
            source_refs = [source_refs]

        claims.append(Claim(
            claim_text=str(c.get("claim_text", ""))[:100],
            evidence_span=str(c.get("evidence_span", ""))[:100],
            evidence_spans=c.get("evidence_spans", []),
            source_refs=[str(s) for s in source_refs if str(s)],
            disagreement_refs=[str(s) for s in c.get("disagreement_refs", [])],
            fact_elements=fact_elements,
            importance=float(c.get("importance", 0.5)),
            source_chunk_ids=[chunk_id],
        ))
    return claims


@trace("Agent2.Understand")
def run_agent2_understand(
    source_bundle: SourceBundle,
    router_decision: RouterDecision,
) -> ContentTree:
    """
    运行 Agent 2 内容理解，输出 ContentTree（含 ContextIndex）。
    v3.1.6：真分块，每块独立 LLM 调用，不再用 text[:6000]+text[-2000:] 截断。
    """
    text = source_bundle.text
    char_count = source_bundle.char_count

    # 计算卡片预算
    min_cards, max_cards = _calculate_card_budget(char_count, router_decision.user_type, source_bundle)
    print(f"[Agent2] 卡片预算范围: {min_cards}-{max_cards} 张（{char_count}字，{router_decision.user_type.value}）")

    # 真分块（带字符偏移）
    chunk_refs = _split_text_chunks_with_offsets(text)
    print(f"[Agent2] 分块数量: {len(chunk_refs)}")

    # ── 逐块 LLM 调用 ────────────────────────────────────────────────────────
    all_claims: list[Claim] = []
    chunk_summaries: dict[str, str] = {}
    chunk_outlines: list[str] = []

    for chunk in chunk_refs:
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            total_chars=char_count,
            chunk_chars=len(chunk.text),
            user_type=router_decision.user_type.value,
            text=chunk.text,
        )
        try:
            raw_result, _ = call_llm(
                user_prompt=user_prompt,
                system_prompt=_SYSTEM_PROMPT,
                expect_json=True,
                label=f"Agent2[{chunk.chunk_id}]",
                max_retries=3,
            )
            chunk_claims = _parse_claims_from_raw(raw_result, source_bundle, chunk.chunk_id, chunk)
            all_claims.extend(chunk_claims)
            summary = str(raw_result.get("chunk_summary", raw_result.get("outline", "")))[:100]
            chunk_summaries[chunk.chunk_id] = summary
            if raw_result.get("outline"):
                chunk_outlines.append(str(raw_result["outline"])[:80])
        except Exception as e:
            print(f"[Agent2] chunk {chunk.chunk_id} LLM 调用失败，跳过: {e}")
            chunk_summaries[chunk.chunk_id] = f"（段落 {chunk.chunk_id} 解析失败）"

    # ── 单块时直接使用，多块时合并 ────────────────────────────────────────────
    if len(chunk_refs) == 1 and all_claims:
        # 单块：直接解析第一块的卡片规划
        try:
            last_raw, _ = call_llm(
                user_prompt=_USER_PROMPT_TEMPLATE.format(
                    total_chars=char_count,
                    chunk_chars=len(chunk_refs[0].text),
                    user_type=router_decision.user_type.value,
                    text=chunk_refs[0].text,
                ),
                system_prompt=_SYSTEM_PROMPT,
                expect_json=True,
                label="Agent2[single]",
                max_retries=2,
            )
            card_budget_raw = int(last_raw.get("card_budget", (min_cards + max_cards) // 2))
            card_slots = last_raw.get("card_slots", [])
            outline = str(last_raw.get("outline", ""))[:200]
        except Exception:
            card_budget_raw = (min_cards + max_cards) // 2
            card_slots = []
            outline = " | ".join(chunk_outlines)[:200]
    elif len(chunk_refs) > 1:
        # 多块：合并 LLM 调用
        summaries_text = "\n".join(
            f"[{cid}] {s}" for cid, s in chunk_summaries.items()
        )
        merge_prompt = _MERGE_PROMPT.format(
            chunk_summaries=summaries_text[:1500],
            total_chars=char_count,
            user_type=router_decision.user_type.value,
        )
        try:
            merge_raw, _ = call_llm(
                user_prompt=merge_prompt,
                system_prompt=_SYSTEM_PROMPT,
                expect_json=True,
                label="Agent2[merge]",
                max_retries=3,
            )
            card_budget_raw = int(merge_raw.get("card_budget", (min_cards + max_cards) // 2))
            card_slots = merge_raw.get("card_slots", [])
            outline = str(merge_raw.get("outline", ""))[:200]
            # 合并后如果 claims 列表也被提供则补充
            if merge_raw.get("claims"):
                pass  # 保持逐块的 claims
        except Exception as e:
            print(f"[Agent2] 合并 LLM 调用失败，使用默认: {e}")
            card_budget_raw = (min_cards + max_cards) // 2
            card_slots = []
            outline = " | ".join(chunk_outlines)[:200]
    else:
        # all_claims 为空（所有块均失败）
        card_budget_raw = (min_cards + max_cards) // 2
        card_slots = []
        outline = "内容理解失败"

    # 强制 card_budget 在范围内，最多 15
    card_budget = max(min_cards, min(max_cards, min(card_budget_raw, 15)))
    if card_budget != card_budget_raw:
        print(f"[Agent2] card_budget 修正: {card_budget_raw} → {card_budget}（范围 {min_cards}-{min(max_cards,15)}）")

    if not card_slots:
        card_slots = _build_default_slots(card_budget)
    if len(card_slots) != card_budget:
        card_slots = _build_default_slots(card_budget)

    # ── 构建 ContextIndex ─────────────────────────────────────────────────────
    source_id = (
        source_bundle.sources[0].source_id
        if source_bundle.sources
        else source_bundle.source_id
    )
    context_index = ContextIndex(
        source_id=source_id,
        full_outline=outline or "（无结构摘要）",
        chunks=chunk_refs,
        chunk_summaries=chunk_summaries,
    )

    content_tree = ContentTree(
        source_bundle=source_bundle,
        claims=all_claims,
        card_budget=card_budget,
        card_slots=card_slots,
        outline=outline,
        context_index=context_index,
    )

    print(f"[Agent2] ContentTree 解析成功: {len(all_claims)} claims, {card_budget} 张卡片预算, {len(chunk_refs)} 块")
    return content_tree


def _build_default_slots(card_budget: int) -> list[dict]:
    """构建默认卡片槽位。"""
    slots = [{"slot_index": 0, "suggested_type": "cover", "content_hint": "封面"}]
    for i in range(1, card_budget - 1):
        slots.append({"slot_index": i, "suggested_type": "section", "content_hint": f"内容{i}"})
    slots.append({"slot_index": card_budget - 1, "suggested_type": "summary", "content_hint": "总结"})
    return slots


def _build_default_content_tree(
    source_bundle: SourceBundle,
    min_cards: int,
    max_cards: int,
) -> ContentTree:
    """LLM 失败时的默认 ContentTree（含最小 ContextIndex）。"""
    budget = (min_cards + max_cards) // 2
    text = source_bundle.text
    chunk_ref = ChunkRef(
        chunk_id="c0",
        char_start=0,
        char_end=len(text),
        text=text,
    )
    source_id = (
        source_bundle.sources[0].source_id
        if source_bundle.sources
        else source_bundle.source_id
    )
    context_index = ContextIndex(
        source_id=source_id,
        full_outline="（内容理解失败，使用默认配置）",
        chunks=[chunk_ref],
        chunk_summaries={"c0": "（解析失败）"},
    )
    return ContentTree(
        source_bundle=source_bundle,
        claims=[],
        card_budget=budget,
        card_slots=_build_default_slots(budget),
        outline="内容理解失败，使用默认配置",
        context_index=context_index,
    )
