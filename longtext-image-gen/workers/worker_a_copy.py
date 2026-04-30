"""
workers/worker_a_copy.py — Worker A 文案生成（v3.1.6）

v3.1.6 改动：
- run_worker_a 增加 context_index / retry_payload 参数
- prompt 从 100 字 evidence 升级为「上下文金字塔」：
    full_outline + chunk 原文（400-600字）+ fact_elements 列表 + 重试错误说明
- 输出 JSON 包含 fact_usages（FactElement 列表）
- 保持 title/body/items/data_label/data_desc/timeline_time 旧字段兼容
- LLM 失败时默认内容保守，不编造事实
"""

from __future__ import annotations

import pathlib
import re
from typing import Optional

import yaml

from infra.tracing import trace
from ir.models import (
    CardContent, CardType, Claim, ContentTree, ContextIndex,
    FactElement, RouterDecision, SourceOffset, UserType,
)
from llm.client import call_llm

_HERE = pathlib.Path(__file__).parent.parent

# 标题党关键词配置文件路径
_CLICKBAIT_CONFIG = _HERE / "configs" / "clickbait_keywords.yaml"

# ── 标题党关键词（默认值，YAML 加载失败时使用）─────────────────────────────
_DEFAULT_CLICKBAIT_PATTERNS = [
    r"震惊[！!]", r"不敢相信", r"万万没想到", r"看完沉默", r"你绝对不知道",
    r"史上最", r"竟然", r"太可怕了", r"吓到了", r"颠覆认知",
]

_clickbait_patterns: list[re.Pattern] = []


def _load_clickbait_patterns() -> list[re.Pattern]:
    global _clickbait_patterns
    if _clickbait_patterns:
        return _clickbait_patterns

    patterns = _DEFAULT_CLICKBAIT_PATTERNS
    try:
        if _CLICKBAIT_CONFIG.exists():
            data = yaml.safe_load(_CLICKBAIT_CONFIG.read_text(encoding="utf-8"))
            patterns = data.get("patterns", _DEFAULT_CLICKBAIT_PATTERNS)
    except Exception as e:
        print(f"[WorkerA] 标题党配置加载失败，使用默认值: {e}")

    _clickbait_patterns = [re.compile(p) for p in patterns]
    return _clickbait_patterns


def _check_clickbait(title: str) -> bool:
    """阶段 1：正则检查标题党。返回 True 表示命中。"""
    patterns = _load_clickbait_patterns()
    for pattern in patterns:
        if pattern.search(title):
            return True
    return False


def _get_context_evidence(
    claim: Claim | None,
    context_index: ContextIndex | None,
    max_chars: int = 500,
) -> str:
    """
    上下文金字塔第 2 层：取 claim 对应 chunk 的 400-600 字局部证据。
    优先 slice_around_offset（100字半径），fallback 取整段 chunk 前 max_chars 字。
    """
    if context_index is None or claim is None:
        return ""

    # 先尝试从 source_chunk_ids 拿对应 chunk
    chunks = context_index.get_chunks_for_claim(claim)
    if not chunks:
        return ""

    chunk = chunks[0]

    # 如果 claim 有 fact_elements 带 source_offset，用 slice_around_offset
    for fe in claim.fact_elements:
        if fe.source_offset and fe.source_offset.char_start >= 0:
            sliced = context_index.slice_around_offset(fe.source_offset, radius=250)
            if sliced:
                return sliced[:max_chars]

    # fallback：取 chunk 前 max_chars 字
    return chunk.text[:max_chars]


def _format_fact_elements(claim: Claim | None) -> str:
    """格式化 fact_elements 列表，供 prompt 引用。"""
    if not claim or not claim.fact_elements:
        return "（无）"
    lines = []
    for fe in claim.fact_elements:
        lines.append(f"  - [{fe.element_type}] {fe.text}（上下文: {fe.context_50[:40]}）")
    return "\n".join(lines)


# ── Prompt 模板 ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_GENERAL = """你是一位社交媒体内容创作者，擅长将严肃内容改写为通俗易懂、引人入胜的信息图文案。

输出严格 JSON：
{
  "title": "卡片标题（≤18字，吸引人但不标题党）",
  "body": "正文（≤80字，通俗易懂）",
  "items": ["列表项1（≤25字）", "列表项2", "列表项3"],
  "data_label": "大字数据（仅 data 类型，如 68%、1200亿）",
  "data_desc": "数据说明（仅 data 类型，≤20字）",
  "timeline_time": "时间节点（仅 timeline 类型）",
  "fact_usages": [
    {"text": "原文精确文字", "element_type": "number|date|entity|percentage", "context_50": "前后25字"}
  ]
}

规则：
- 标题≤18字，正文≤80字
- 内容必须来自原文，不得编造
- fact_usages 中的 text 必须与原文证据一字不差，不能截断数字或改写实体名
- 如数字在原文中是 758631，fact_usages 中必须是 758631，不能写成 75万863"""

_SYSTEM_PROMPT_PROFESSIONAL = """你是一位专业内容分析师，为专业读者提供高密度、精准的信息图文案。

输出严格 JSON：
{
  "title": "卡片标题（≤18字，专业准确）",
  "body": "正文（≤80字，术语准确，密度高）",
  "items": ["列表项1（≤30字，含数据）", "列表项2", "列表项3"],
  "data_label": "大字数据（仅 data 类型，如 68%、1200亿）",
  "data_desc": "数据说明（仅 data 类型，≤20字）",
  "timeline_time": "时间节点（仅 timeline 类型）",
  "fact_usages": [
    {"text": "原文精确文字", "element_type": "number|date|entity|percentage", "context_50": "前后25字"}
  ]
}

规则：
- 标题≤18字，正文≤80字
- 保留专业术语，内容必须来自原文
- fact_usages 中的 text 必须与原文证据完整一致，不能截断或改写"""

_USER_PROMPT_TEMPLATE = """为以下卡片槽位生成文案（卡片类型：{card_type}，整体主题：{topic}）：

## 核心声明
{claim_text}

## 文章整体结构（full_outline）
{full_outline}

## 对应原文局部证据（来自 chunk {chunk_id}，~500字）
{local_evidence}

## 关键事实元素（必须精确抄录，不可改写）
{fact_elements}
{retry_note}
直接输出 JSON，不要解释。"""


@trace("WorkerA.Copy")
def run_worker_a(
    slot: dict,
    content_tree: ContentTree,
    router_decision: RouterDecision,
    slot_index: int,
    claim: Optional[Claim] = None,
    context_index: Optional[ContextIndex] = None,
    retry_payload: Optional[dict] = None,
) -> CardContent:
    """
    运行 Worker A 文案生成（v3.1.6 上下文金字塔）。

    Args:
        slot: 卡片槽位配置
        content_tree: ContentTree（含 context_index）
        router_decision: 路由决策
        slot_index: 槽位下标
        claim: 对应 Claim（可选）
        context_index: 上下文索引（可选，优先使用，fallback 到 content_tree.context_index）
        retry_payload: 重试负载（含上次失败信息）
    """
    card_type = slot.get("suggested_type", "section")
    content_hint = slot.get("content_hint", "")

    # context_index 优先级：参数 > content_tree.context_index
    ctx_idx = context_index or content_tree.context_index

    # 获取对应 Claim
    if claim is None and content_tree.claims:
        claim_idx = min(slot_index, len(content_tree.claims) - 1)
        claim = content_tree.claims[claim_idx]

    claim_text = claim.claim_text if claim else content_hint
    evidence_span = claim.evidence_span if claim else ""

    # ── 上下文金字塔 ──────────────────────────────────────────────────────────
    full_outline = (ctx_idx.full_outline if ctx_idx else content_tree.outline or "")[:300]

    # 第 2 层：chunk 局部原文（400-600字）
    local_evidence = _get_context_evidence(claim, ctx_idx, max_chars=550)
    if not local_evidence:
        # fallback 到 evidence_span
        local_evidence = evidence_span[:200] if evidence_span else content_hint[:100]

    # 确定 chunk_id
    chunk_id = "c0"
    if claim and claim.source_chunk_ids:
        chunk_id = claim.source_chunk_ids[0]

    # 第 3 层：fact_elements 列表
    fact_elements_text = _format_fact_elements(claim)

    # 重试说明
    retry_note = ""
    if retry_payload:
        errors = retry_payload.get("failed_elements", [])
        failures = retry_payload.get("failures", [])
        if failures:
            error_lines = [f"  - {f.get('element','?')} ({f.get('error_type','?')}): {f.get('element_type','?')}" for f in failures[:5]]
            retry_note = "\n## 上次核验失败（请避免重复这些问题）\n" + "\n".join(error_lines) + "\n"
        elif errors:
            retry_note = f"\n## 上次核验失败元素（请修正）\n  {', '.join(str(e) for e in errors[:5])}\n"

    # 选择 prompt
    system_prompt = (
        _SYSTEM_PROMPT_PROFESSIONAL
        if router_decision.user_type == UserType.PROFESSIONAL
        else _SYSTEM_PROMPT_GENERAL
    )

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        card_type=card_type,
        topic=content_tree.outline[:50] if content_tree.outline else "主题内容",
        claim_text=claim_text[:100],
        full_outline=full_outline,
        local_evidence=local_evidence,
        chunk_id=chunk_id,
        fact_elements=fact_elements_text,
        retry_note=retry_note,
    )

    try:
        raw_result, _ = call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            expect_json=True,
            label=f"WorkerA[{slot_index}]",
            max_retries=2,
        )
    except Exception as e:
        print(f"[WorkerA] 卡片 {slot_index} LLM 调用失败，使用默认内容: {e}")
        return _build_default_content(card_type, content_hint)

    # ── 解析并强制截断 ────────────────────────────────────────────────────────
    try:
        title = str(raw_result.get("title", content_hint))[:18]
        body = str(raw_result.get("body", ""))[:80]
        items = [str(i)[:30] for i in raw_result.get("items", [])[:5]]
        data_label = str(raw_result.get("data_label", ""))
        data_desc = str(raw_result.get("data_desc", ""))[:20]
        timeline_time = str(raw_result.get("timeline_time", ""))

        # 解析 fact_usages
        fact_usages: list[FactElement] = []
        for fu in raw_result.get("fact_usages", []):
            try:
                fu_text = str(fu.get("text", ""))
                if fu_text:
                    # 尝试在 local_evidence 中找到对应 offset
                    source_offset: Optional[SourceOffset] = None
                    if ctx_idx and claim and claim.source_chunk_ids:
                        chunk = ctx_idx.get_chunk(claim.source_chunk_ids[0])
                        if chunk:
                            pos = chunk.text.find(fu_text)
                            if pos >= 0:
                                source_offset = SourceOffset(
                                    chunk_id=chunk.chunk_id,
                                    char_start=chunk.char_start + pos,
                                    char_end=chunk.char_start + pos + len(fu_text),
                                    text=fu_text,
                                )
                    fact_usages.append(FactElement(
                        text=fu_text,
                        element_type=str(fu.get("element_type", "entity")),
                        context_50=str(fu.get("context_50", ""))[:60],
                        source_offset=source_offset,
                    ))
            except Exception:
                pass

        # 阶段 1 标题党检查
        title_warning = _check_clickbait(title)
        if title_warning:
            print(f"[WorkerA] 卡片 {slot_index} 标题党警告: '{title}'")

        content = CardContent(
            title=title,
            body=body,
            items=items,
            data_label=data_label,
            data_desc=data_desc,
            timeline_time=timeline_time,
            title_warning=title_warning,
            fact_usages=fact_usages,
        )

        # data 类型 data_label 兜底
        if card_type == "data" and not content.data_label.strip():
            fallback = body[:10] if body else "N/A"
            print(f"[WorkerA] data_label 为空，补填: '{fallback}'")
            content = content.model_copy(update={"data_label": fallback})

        return content

    except Exception as e:
        print(f"[WorkerA] CardContent 解析异常: {e}")
        return _build_default_content(card_type, content_hint)


def _build_default_content(card_type: str, hint: str) -> CardContent:
    """LLM 失败时的默认文案（保守，不编造事实）。"""
    title = hint[:18] if hint else f"{card_type}卡片"
    return CardContent(
        title=title,
        body="内容整理中，请参阅原文。",
        items=[],
        data_label="N/A" if card_type == "data" else "",
        fact_usages=[],
    )
