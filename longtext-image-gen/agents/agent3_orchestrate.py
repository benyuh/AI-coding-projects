"""
agents/agent3_orchestrate.py — Agent 3 Orchestrator（v3.1.6）

v3.1.6 改动：
- run_agent3_orchestrate 增加 context_index 可选参数
- 每张卡执行 WorkerA → WorkerB 闭环，REJECTED 时最多重试 2 次
- 超限后生成 text-only fallback 卡（is_text_only_fallback=True）
- Card.fact_usages 从 CardContent.fact_usages 复制
- Blueprint.context_index 赋值
- WorkerC/D 保持兼容
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import uuid
from typing import Optional

from infra.config import WORKER_A_MAX_CONCURRENCY
from infra.tracing import trace
from ir.models import (
    BCheck, BCheckStatus, Blueprint, Card, CardContent,
    ContentTree, ContextIndex, RouterDecision, SourceBundle, StyleTokens, VisualSpec,
)
from workers.worker_a_copy import run_worker_a
from workers.worker_b_factcheck import run_worker_b
from workers.worker_c_visual import run_worker_c
from workers.worker_d_style import run_worker_d

# 每张卡片 A→B 最大重试次数（demo_safe_mode 下使用更高值）
def _get_max_card_retries() -> int:
    try:
        from infra.config import DEMO_SAFE_MODE, DEMO_MAX_CARD_RETRIES
        return DEMO_MAX_CARD_RETRIES if DEMO_SAFE_MODE else 2
    except ImportError:
        return 2

_MAX_CARD_RETRIES = 2  # 保留作为默认，实际运行时通过 _get_max_card_retries() 获取


def _build_retry_payload(b_check: BCheck) -> dict:
    """将 BCheck 的失败信息转换为 retry_payload。"""
    failures = [
        {
            "element": f.element,
            "element_type": f.element_type,
            "error_type": f.error_type,
            "card_field": f.card_field,
        }
        for f in b_check.failures
    ]
    return {
        "failed_elements": b_check.failed_elements,
        "failures": failures,
    }


def _build_text_only_fallback(
    slot: dict,
    claim,
    b_check: BCheck,
) -> CardContent:
    """
    生成 text-only fallback 文案（保守，不编造事实）。
    使用 claim.evidence_span 或 claim.claim_text 构造简单摘要。
    """
    card_type = slot.get("suggested_type", "section")
    hint = slot.get("content_hint", "")

    if claim:
        title = (claim.claim_text[:18] if claim.claim_text else hint[:18]) or f"{card_type}卡片"
        body = claim.evidence_span[:80] if claim.evidence_span else claim.claim_text[:80]
    else:
        title = hint[:18] or f"{card_type}卡片"
        body = "详见原文。"

    return CardContent(
        title=title,
        body=body,
        items=[],
        data_label="",
        fact_usages=[],
    )


async def _run_card_with_ab_loop(
    slot: dict,
    slot_index: int,
    content_tree: ContentTree,
    router_decision: RouterDecision,
    source_bundle: SourceBundle,
    context_index: Optional[ContextIndex],
    semaphore: asyncio.Semaphore,
    loop: asyncio.AbstractEventLoop,
    initial_retry_payload: Optional[dict] = None,
) -> tuple[CardContent, BCheck]:
    """
    单卡 A→B 闭环（含重试），返回 (CardContent, BCheck)。
    每张卡 WorkerA 最多调用 1 + _MAX_CARD_RETRIES 次。
    initial_retry_payload：Gate1 targeted retry 时的初始 payload（直接带入第一次 WorkerA 调用）。
    """
    async with semaphore:
        claim = None
        if content_tree.claims:
            claim_idx = min(slot_index, len(content_tree.claims) - 1)
            claim = content_tree.claims[claim_idx]

        retry_payload: Optional[dict] = initial_retry_payload  # 可能是 Gate1 注入的
        last_content: Optional[CardContent] = None
        last_b_check: Optional[BCheck] = None

        max_retries = _get_max_card_retries()
        for attempt in range(1 + max_retries):
            # WorkerA
            content = await loop.run_in_executor(
                None,
                lambda s=slot, ct=content_tree, rd=router_decision, i=slot_index, c=claim, ci=context_index, rp=retry_payload:
                    run_worker_a(s, ct, rd, i, c, ci, rp),
            )
            last_content = content

            # WorkerB
            b_check = await loop.run_in_executor(
                None,
                lambda cc=content, src=source_bundle.text, cl=claim, fu=content.fact_usages, ci=context_index:
                    run_worker_b(cc, src, cl, fu, ci),
            )
            last_b_check = b_check

            if b_check.status != BCheckStatus.REJECTED:
                # PASSED 或 DEGRADED → 不再重试
                break

            if attempt < max_retries:
                print(f"[Agent3] 卡片 {slot_index} 第{attempt+1}次 REJECTED，重试...")
                retry_payload = _build_retry_payload(b_check)
            else:
                # 超限，生成 text-only fallback
                print(f"[Agent3] 卡片 {slot_index} 重试耗尽，降级为 text-only fallback")
                fallback_content = _build_text_only_fallback(slot, claim, b_check)
                fallback_b_check = b_check.model_copy(update={
                    "status": BCheckStatus.DEGRADED,
                    "retry_count": max_retries,
                })
                return fallback_content, fallback_b_check

        return last_content, last_b_check


@trace("Agent3.Orchestrate")
def run_agent3_orchestrate(
    source_bundle: SourceBundle,
    router_decision: RouterDecision,
    content_tree: ContentTree,
    context_index: Optional[ContextIndex] = None,
    gate1_retry_payload: Optional[dict] = None,
) -> Blueprint:
    """
    运行 Agent 3 Orchestrator，组装完整 Blueprint。

    v3.1.6：每张卡执行 WorkerA→WorkerB 闭环，REJECTED 时重试（最多 2 次）。
    gate1_retry_payload（来自 Gate1 faithfulness 失败）：对 failed_card_ids 做 targeted retry。

    并发模型：
    - Worker D 不依赖其他 Worker，最先运行
    - Worker A/B 每张卡串行（A→B），各卡并发（semaphore 限制）
    - Worker C 在所有卡片 A/B 完成后运行
    """
    # context_index 优先级：参数 > content_tree.context_index
    ctx_idx = context_index or content_tree.context_index

    return asyncio.run(_orchestrate_async(
        source_bundle, router_decision, content_tree, ctx_idx, gate1_retry_payload
    ))


async def _orchestrate_async(
    source_bundle: SourceBundle,
    router_decision: RouterDecision,
    content_tree: ContentTree,
    context_index: Optional[ContextIndex],
    gate1_retry_payload: Optional[dict] = None,
) -> Blueprint:
    """异步 Orchestrate 主逻辑。"""
    slots = content_tree.card_slots
    total_cards = len(slots)

    # 从 gate1_retry_payload 提取需要 targeted retry 的卡片 ID 集合
    gate1_failed_ids: set[str] = set()
    gate1_failed_facts: dict[str, list[dict]] = {}  # card_id -> list[FailedFact dict]
    if gate1_retry_payload:
        gate1_failed_ids = {str(x) for x in gate1_retry_payload.get("failed_card_ids", [])}
        for ff in gate1_retry_payload.get("failed_facts", []):
            cid = str(ff.get("card_id", ""))
            gate1_failed_facts.setdefault(cid, []).append(ff)
        if gate1_failed_ids:
            print(f"[Agent3] Gate1 targeted retry 卡片: {sorted(gate1_failed_ids)}")

    print(f"[Agent3] 开始编排 {total_cards} 张卡片（含 A→B 闭环）...")

    loop = asyncio.get_running_loop()

    # ── Worker D（纯规则，先行）──────────────────────────────────────────────
    style_tokens_task = loop.run_in_executor(
        None, lambda: run_worker_d(router_decision)
    )

    # ── 每张卡 A→B 闭环（并发，semaphore 限流）────────────────────────────────
    semaphore = asyncio.Semaphore(WORKER_A_MAX_CONCURRENCY)
    card_tasks = []
    for i, slot in enumerate(slots):
        # Gate1 targeted retry：对失败卡片注入初始 retry_payload
        initial_retry_payload: Optional[dict] = None
        card_id_str = str(i)
        if card_id_str in gate1_failed_ids:
            failed_facts_for_card = gate1_failed_facts.get(card_id_str, [])
            initial_retry_payload = {
                "gate1_failed_facts": failed_facts_for_card,
                "failed_elements": [ff.get("hallucinated_text", "") for ff in failed_facts_for_card],
                "failures": [
                    {
                        "element": ff.get("hallucinated_text", ""),
                        "element_type": "entity",
                        "error_type": "gate1_faithfulness",
                        "card_field": ff.get("card_field", "body"),
                    }
                    for ff in failed_facts_for_card
                ],
            }
            print(f"[Agent3] 卡片 {i} 注入 Gate1 retry_payload: {len(failed_facts_for_card)} 条失败事实")
        card_tasks.append(
            _run_card_with_ab_loop(
                slot, i, content_tree, router_decision, source_bundle,
                context_index, semaphore, loop,
                initial_retry_payload=initial_retry_payload,
            )
        )
    card_results = await asyncio.gather(*card_tasks)
    card_contents = [r[0] for r in card_results]
    b_checks = [r[1] for r in card_results]

    print(f"[Agent3] Worker A/B 全部完成，{len(card_contents)} 张卡片")

    # ── Worker C（依赖 A 输出，与 D 并行）────────────────────────────────────
    visual_specs_task = loop.run_in_executor(
        None,
        lambda: run_worker_c(slots, card_contents, router_decision),
    )

    # 等待 C 和 D 都完成
    visual_specs, style_tokens = await asyncio.gather(visual_specs_task, style_tokens_task)
    print(f"[Agent3] Worker C/D 全部完成")

    # ── 组装 Blueprint ─────────────────────────────────────────────────────────
    blueprint_id = str(uuid.uuid4())[:8]
    cards = []
    for i, (slot, content, b_check, visual_spec) in enumerate(
        zip(slots, card_contents, b_checks, visual_specs)
    ):
        claim_idx = min(i, len(content_tree.claims) - 1) if content_tree.claims else -1
        # 判断是否 text-only fallback
        is_text_only = (b_check.status == BCheckStatus.DEGRADED and b_check.retry_count >= _get_max_card_retries())

        card = Card(
            card_index=i,
            content=content,
            visual_spec=visual_spec,
            b_check=b_check,
            source_claim_index=claim_idx,
            fact_usages=list(content.fact_usages),
            is_text_only_fallback=is_text_only,
        )
        cards.append(card)

    blueprint = Blueprint(
        blueprint_id=blueprint_id,
        source_bundle=source_bundle,
        router_decision=router_decision,
        content_tree=content_tree,
        context_index=context_index,
        style_tokens=style_tokens,
        cards=cards,
        created_at=datetime.datetime.now().isoformat(),
    )

    # 统计
    passed = sum(1 for c in blueprint.cards if c.b_check.status == BCheckStatus.PASSED)
    degraded = sum(1 for c in blueprint.cards if c.b_check.status == BCheckStatus.DEGRADED)
    rejected = sum(1 for c in blueprint.cards if c.b_check.status == BCheckStatus.REJECTED)
    fallback = sum(1 for c in blueprint.cards if c.is_text_only_fallback)
    print(
        f"[Agent3] Blueprint 组装完成: {len(cards)} 张卡片 | "
        f"B-Check: {passed} passed, {degraded} degraded, {rejected} rejected | "
        f"text-only fallback: {fallback}"
    )

    return blueprint
