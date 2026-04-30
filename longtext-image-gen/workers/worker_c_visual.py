"""
workers/worker_c_visual.py — Worker C 视觉结构（v3.1.2）

功能：
- 按 PRD 4.5.3 决策树选 visual_type（8 种枚举）
- 构建 structured_data
- 全局规则 G1：text_with_icon 占比 ≤ 60%，同类型连续 ≤ 3
- 纯规则，不调 LLM
"""

from __future__ import annotations

from typing import Optional

from infra.tracing import trace
from ir.models import (
    CardContent, CardType, ContentTree, RouterDecision,
    VisualSpec, VisualType
)


def _decide_visual_type(
    slot: dict,
    card_content: CardContent,
    slot_index: int,
    total_cards: int,
) -> VisualType:
    """
    按 PRD 4.5.3 决策树决定 visual_type。
    """
    card_type = slot.get("suggested_type", "section")

    # 封面卡 → cover_hero
    if card_type == "cover" or slot_index == 0:
        return VisualType.COVER_HERO

    # 总结卡 → text_with_icon
    if card_type == "summary" or slot_index == total_cards - 1:
        return VisualType.TEXT_WITH_ICON

    # 有明确数据标签 → data_highlight
    if card_type == "data" or card_content.data_label:
        return VisualType.DATA_HIGHLIGHT

    # 有时间字段 → timeline_vertical
    if card_type == "timeline" or card_content.timeline_time:
        return VisualType.TIMELINE_VERTICAL

    # 有 items 且数量 >= 3 → text_with_icon
    if len(card_content.items) >= 3:
        return VisualType.TEXT_WITH_ICON

    # 默认 → text_with_icon
    return VisualType.TEXT_WITH_ICON


def _decide_card_type(slot: dict, slot_index: int, total_cards: int) -> CardType:
    """从 slot 推断 CardType。"""
    suggested = slot.get("suggested_type", "section")
    mapping = {
        "cover": CardType.COVER,
        "data": CardType.DATA,
        "timeline": CardType.TIMELINE,
        "summary": CardType.SUMMARY,
        "section": CardType.SECTION,
    }
    if slot_index == 0:
        return CardType.COVER
    if slot_index == total_cards - 1:
        return CardType.SUMMARY
    return mapping.get(suggested, CardType.SECTION)


def _build_structured_data(
    visual_type: VisualType,
    card_content: CardContent,
    slot: dict,
) -> dict:
    """为不同 visual_type 构建 structured_data。"""
    if visual_type == VisualType.DATA_HIGHLIGHT:
        return {
            "label": card_content.data_label,
            "desc": card_content.data_desc,
            "unit": "",
        }
    if visual_type == VisualType.TIMELINE_VERTICAL:
        return {
            "nodes": [
                {
                    "time": card_content.timeline_time,
                    "event": card_content.title,
                    "detail": card_content.body,
                }
            ]
        }
    if visual_type == VisualType.TEXT_WITH_ICON:
        return {
            "items": card_content.items,
        }
    # 其他类型返回空结构
    return {}


@trace("WorkerC.Visual")
def run_worker_c(
    slots: list[dict],
    card_contents: list[CardContent],
    router_decision: RouterDecision,
) -> list[VisualSpec]:
    """
    运行 Worker C 视觉结构决策。
    应用全局规则 G1 后返回 VisualSpec 列表。
    """
    total_cards = len(slots)
    visual_specs = []

    # 第一轮：为每张卡片决定 visual_type
    for i, (slot, content) in enumerate(zip(slots, card_contents)):
        vt = _decide_visual_type(slot, content, i, total_cards)
        ct = _decide_card_type(slot, i, total_cards)
        structured = _build_structured_data(vt, content, slot)
        visual_specs.append(VisualSpec(
            visual_type=vt,
            card_type=ct,
            structured_data=structured,
        ))

    # 第二轮：应用全局规则 G1
    visual_specs = _apply_g1_constraints(visual_specs)

    # 统计 visual_type 分布
    type_counts: dict[str, int] = {}
    for vs in visual_specs:
        k = vs.visual_type.value
        type_counts[k] = type_counts.get(k, 0) + 1
    print(f"[WorkerC] visual_type 分布: {type_counts}")

    return visual_specs


def _apply_g1_constraints(specs: list[VisualSpec]) -> list[VisualSpec]:
    """
    全局规则 G1：
    1. text_with_icon 占比 ≤ 60%
    2. 同类型连续 ≤ 3
    """
    total = len(specs)
    if total == 0:
        return specs

    # 规则 1：text_with_icon 占比检查
    twi_count = sum(1 for s in specs if s.visual_type == VisualType.TEXT_WITH_ICON)
    twi_ratio = twi_count / total
    if twi_ratio > 0.6:
        print(f"[WorkerC] G1: text_with_icon 占比 {twi_ratio:.0%} > 60%，调整中...")
        # 将部分 text_with_icon 转为 data_highlight（跳过首尾）
        adjusted = 0
        for i, spec in enumerate(specs):
            if adjusted >= twi_count - int(total * 0.6):
                break
            if (spec.visual_type == VisualType.TEXT_WITH_ICON
                    and spec.card_type not in (CardType.COVER, CardType.SUMMARY)
                    and i > 0 and i < total - 1):
                # 检查是否有 items（有则保留）
                if not spec.structured_data.get("items"):
                    specs[i] = spec.model_copy(
                        update={"visual_type": VisualType.DATA_HIGHLIGHT}
                    )
                    adjusted += 1

    # 规则 2：同类型连续 ≤ 3
    for i in range(2, total):
        if (specs[i].visual_type == specs[i-1].visual_type == specs[i-2].visual_type
                and specs[i].visual_type == VisualType.TEXT_WITH_ICON):
            # 打断连续
            if specs[i].card_type not in (CardType.COVER, CardType.SUMMARY):
                specs[i] = specs[i].model_copy(
                    update={"visual_type": VisualType.TIMELINE_VERTICAL}
                )
                print(f"[WorkerC] G1: 打断连续 text_with_icon at index {i}")

    return specs
