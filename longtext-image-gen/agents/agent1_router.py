"""
agents/agent1_router.py — Agent 1 路由决策（v3.1.6）

v3.1.6 改动：
- run_agent1_router 增加 target_platform 参数
- 修 prompt：娱乐/生活/种草 → xiaohongshu；严肃资讯/商业 → wechat_moments
- target_platform 非空且非 auto 时强制覆盖 platform 路由结果
"""

from __future__ import annotations

import json
from typing import Optional

from infra.tracing import trace
from ir.models import (
    ArticleType, NarrativeStructure, Platform, RouterDecision,
    RiskLevel, SourceBundle, UserType
)
from llm.client import call_llm

# 预检常量
MIN_CHARS = 200
MAX_CHARS = 30_000

# 风险类别关键词（粗筛）
_RISK_PATTERNS = {
    "legal": ["律师函", "法律责任", "诉讼", "判决书", "起诉"],
    "medical": ["用药剂量", "手术方案", "医疗诊断", "处方", "临床试验结论"],
    "poetry": ["五言", "七言绝句", "词牌名", "诗经"],
}

# target_platform → Platform 映射（支持多种别名）
_PLATFORM_MAP: dict[str, Platform] = {
    "xiaohongshu": Platform.XIAOHONGSHU,
    "xhs": Platform.XIAOHONGSHU,
    "小红书": Platform.XIAOHONGSHU,
    "wechat_moments": Platform.WECHAT_MOMENTS,
    "wechat": Platform.WECHAT_MOMENTS,
    "wechat_official": Platform.WECHAT_MOMENTS,
    "朋友圈": Platform.WECHAT_MOMENTS,
    "微信": Platform.WECHAT_MOMENTS,
}

_SYSTEM_PROMPT = """你是一位内容路由专家。分析文章，输出路由决策 JSON（不含 Markdown 包裹）：

{
  "article_type": "news|analysis|research|policy|tutorial|opinion|unknown",
  "user_type": "professional|general",
  "platform": "xiaohongshu|wechat_moments",
  "narrative_structure": "pyramid_argument|problem_solution_action|chronological_timeline|before_after_comparison|thought_journey|lin_style_explainer|pyramid_research_pro|entity_relation_map|consensus_disagreement_map|multi_source_timeline",
  "risk_level": "safe|borderline|blocked",
  "risk_reason": "如有风险，简述原因（≤30字）",
  "style_hint": "clean_business|xiaohongshu_warm|data_journalism|tech_minimal|magazine_editorial"
}

## 判断规则
- article_type：根据文章内容主题判断
- user_type：有大量专业术语 → professional；通俗易懂 → general
- platform：
    * 娱乐/生活/种草/情感/美食/旅行/时尚 → xiaohongshu
    * 严肃资讯/商业/科技/政策/财经/分析报告 → wechat_moments
- narrative_structure：根据文章结构选择最匹配的叙事框架
- risk_level：涉及法律判定/医疗处方/个人隐私/纯诗歌 → blocked；仅轻微边界 → borderline；其余 → safe
"""

_USER_PROMPT_TEMPLATE = """分析以下文章（约{char_count}字），输出路由决策JSON：

---
{text_preview}
---

直接输出JSON，不要解释。"""


@trace("Agent1.Router")
def run_agent1_router(
    source_bundle: SourceBundle,
    target_platform: Optional[str] = None,
) -> RouterDecision:
    """
    运行 Agent 1 路由决策。

    v3.1.6：支持 target_platform 强制覆盖 platform 路由结果。
    """
    text = source_bundle.text
    char_count = source_bundle.char_count

    # ── 预检短路 ─────────────────────────────────────────────────────────────
    if char_count < MIN_CHARS:
        print(f"[Agent1] TEXT_TOO_SHORT: {char_count} 字 < {MIN_CHARS}")
        return RouterDecision(
            risk_level=RiskLevel.BLOCKED,
            risk_reason="文章过短",
            skip_reason="TEXT_TOO_SHORT",
        )

    if char_count > MAX_CHARS:
        print(f"[Agent1] TEXT_TOO_LONG: {char_count} 字 > {MAX_CHARS}（截断处理）")
        source_bundle = SourceBundle(
            text=text[:MAX_CHARS],
            source_id=source_bundle.source_id,
            char_count=MAX_CHARS,
        )
        text = source_bundle.text

    # ── 粗筛风险关键词 ───────────────────────────────────────────────────────
    for category, keywords in _RISK_PATTERNS.items():
        for kw in keywords:
            if kw in text:
                print(f"[Agent1] 风险粗筛命中: category={category}, keyword={kw}")

    # ── LLM 路由决策 ─────────────────────────────────────────────────────────
    text_preview = text[:3000]
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        char_count=char_count,
        text_preview=text_preview,
    )

    try:
        raw_decision, _ = call_llm(
            user_prompt=user_prompt,
            system_prompt=_SYSTEM_PROMPT,
            expect_json=True,
            label="Agent1",
            max_retries=3,
        )
    except Exception as e:
        print(f"[Agent1] LLM 调用失败，使用默认决策: {e}")
        decision = RouterDecision()
        return _apply_target_platform(decision, target_platform)

    # ── 解析 RouterDecision ─────────────────────────────────────────────────
    try:
        decision = RouterDecision(
            article_type=ArticleType(raw_decision.get("article_type", "unknown")),
            user_type=UserType(raw_decision.get("user_type", "general")),
            platform=Platform(raw_decision.get("platform", "xiaohongshu")),
            narrative_structure=NarrativeStructure(
                raw_decision.get("narrative_structure", "pyramid_argument")
            ),
            risk_level=RiskLevel(raw_decision.get("risk_level", "safe")),
            risk_reason=str(raw_decision.get("risk_reason", ""))[:60],
            style_hint=str(raw_decision.get("style_hint", "clean_business")),
        )
    except (ValueError, KeyError) as e:
        print(f"[Agent1] RouterDecision 解析异常，使用默认值: {e}")
        decision = RouterDecision()

    # ── 强制 target_platform 覆盖 ─────────────────────────────────────────
    decision = _apply_target_platform(decision, target_platform)

    print(
        f"[Agent1] 决策: type={decision.article_type.value}, "
        f"structure={decision.narrative_structure.value}, "
        f"platform={decision.platform.value}, "
        f"risk={decision.risk_level.value}, "
        f"style={decision.style_hint}"
    )
    return decision


def _apply_target_platform(
    decision: RouterDecision,
    target_platform: Optional[str],
) -> RouterDecision:
    """
    若 target_platform 非空且非 auto，强制覆盖 RouterDecision.platform。
    """
    if not target_platform or target_platform.lower() == "auto":
        return decision

    forced = _PLATFORM_MAP.get(target_platform.lower())
    if forced and forced != decision.platform:
        print(f"[Agent1] 强制平台: {decision.platform.value} → {forced.value}（target_platform={target_platform}）")
        decision = decision.model_copy(update={"platform": forced})

    return decision
