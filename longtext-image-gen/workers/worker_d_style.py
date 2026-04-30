"""
workers/worker_d_style.py — Worker D 风格选择（v3.1.2）

功能：
- 纯规则，不调 LLM
- 从 PRD 4.4.2 结构-风格兼容矩阵查表
- 加载 render/styles/{style_id}.yaml
- 输出 StyleTokens
"""

from __future__ import annotations

import pathlib
from typing import Optional

import yaml

from infra.tracing import trace
from ir.models import (
    ArticleType, NarrativeStructure, Platform,
    RouterDecision, StyleTokens, UserType
)

_HERE = pathlib.Path(__file__).parent.parent
_STYLES_DIR = _HERE / "render" / "styles"

# ── 结构-风格兼容矩阵（PRD 4.4.2）───────────────────────────────────────────
# 格式：(narrative_structure, article_type, user_type) → style_id
_STYLE_MATRIX: dict[tuple, str] = {
    # 金字塔/论证 + 分析/研究 + 专业 → 数据新闻
    (NarrativeStructure.PYRAMID_ARGUMENT, ArticleType.ANALYSIS, UserType.PROFESSIONAL): "data_journalism",
    (NarrativeStructure.PYRAMID_ARGUMENT, ArticleType.RESEARCH, UserType.PROFESSIONAL): "data_journalism",
    (NarrativeStructure.PYRAMID_RESEARCH_PRO, ArticleType.RESEARCH, UserType.PROFESSIONAL): "data_journalism",
    # 时间线 + 新闻/政策 → 商业简洁
    (NarrativeStructure.CHRONOLOGICAL_TIMELINE, ArticleType.NEWS, UserType.GENERAL): "clean_business",
    (NarrativeStructure.CHRONOLOGICAL_TIMELINE, ArticleType.POLICY, UserType.GENERAL): "clean_business",
    # 多源时间线 + 专业 → 技术极简
    (NarrativeStructure.MULTI_SOURCE_TIMELINE, ArticleType.ANALYSIS, UserType.PROFESSIONAL): "tech_minimal",
    # 对比/前后 + 通用 → 小红书暖色
    (NarrativeStructure.BEFORE_AFTER_COMPARISON, ArticleType.OPINION, UserType.GENERAL): "xiaohongshu_warm",
    # 思想旅程/林氏解析 → 小红书暖色
    (NarrativeStructure.THOUGHT_JOURNEY, ArticleType.OPINION, UserType.GENERAL): "xiaohongshu_warm",
    (NarrativeStructure.LIN_STYLE_EXPLAINER, ArticleType.TUTORIAL, UserType.GENERAL): "xiaohongshu_warm",
}

# 回退规则：按 narrative_structure
_STRUCTURE_FALLBACK: dict[NarrativeStructure, str] = {
    NarrativeStructure.PYRAMID_ARGUMENT: "clean_business",
    NarrativeStructure.PYRAMID_RESEARCH_PRO: "data_journalism",
    NarrativeStructure.CHRONOLOGICAL_TIMELINE: "clean_business",
    NarrativeStructure.MULTI_SOURCE_TIMELINE: "tech_minimal",
    NarrativeStructure.BEFORE_AFTER_COMPARISON: "xiaohongshu_warm",
    NarrativeStructure.THOUGHT_JOURNEY: "xiaohongshu_warm",
    NarrativeStructure.LIN_STYLE_EXPLAINER: "xiaohongshu_warm",
    NarrativeStructure.ENTITY_RELATION_MAP: "tech_minimal",
    NarrativeStructure.CONSENSUS_DISAGREEMENT_MAP: "magazine_editorial",
    NarrativeStructure.PROBLEM_SOLUTION_ACTION: "clean_business",
}

# ── 默认 StyleTokens（clean_business）────────────────────────────────────────
_DEFAULT_STYLE_TOKENS = StyleTokens(
    style_id="clean_business",
    primary_color="#FF6B6B",
    secondary_color="#FF8E53",
    accent_color="#FFB347",
    background_color="#FFF8F0",
    text_color="#1A1A1A",
    font_size_base=26,
    border_radius=20,
    card_shadow="0 2px 16px rgba(0,0,0,0.06)",
    cover_gradient="linear-gradient(135deg, #FF6B6B 0%, #FF8E53 50%, #FFB347 100%)",
    summary_gradient="linear-gradient(135deg, #667EEA 0%, #764BA2 100%)",
)


def _load_style_yaml(style_id: str) -> Optional[dict]:
    """加载风格 YAML 文件。"""
    yaml_path = _STYLES_DIR / f"{style_id}.yaml"
    if not yaml_path.exists():
        return None
    try:
        return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WorkerD] 风格 YAML 加载失败 ({style_id}): {e}")
        return None


def _apply_platform_tweaks(tokens: StyleTokens, platform: Platform) -> StyleTokens:
    """按平台微调风格。"""
    if platform == Platform.WECHAT_MOMENTS:
        # 朋友圈：字号稍小，圆角稍大
        return tokens.model_copy(update={
            "font_size_base": max(22, tokens.font_size_base - 2),
            "border_radius": tokens.border_radius + 4,
        })
    return tokens


@trace("WorkerD.Style")
def run_worker_d(router_decision: RouterDecision) -> StyleTokens:
    """
    运行 Worker D 风格选择。
    纯规则，不调 LLM。
    """
    # 1. 查矩阵
    matrix_key = (
        router_decision.narrative_structure,
        router_decision.article_type,
        router_decision.user_type,
    )
    style_id = _STYLE_MATRIX.get(matrix_key)

    # 2. 结构回退
    if not style_id:
        style_id = _STRUCTURE_FALLBACK.get(
            router_decision.narrative_structure,
            router_decision.style_hint or "clean_business"
        )

    # 3. Agent 1 style_hint 覆盖（如果 hint 有对应 YAML）
    hint_path = _STYLES_DIR / f"{router_decision.style_hint}.yaml"
    if router_decision.style_hint and hint_path.exists():
        style_id = router_decision.style_hint

    print(f"[WorkerD] 选定风格: {style_id}")

    # 4. 加载 YAML
    style_data = _load_style_yaml(style_id)
    if style_data is None:
        print(f"[WorkerD] 风格 YAML 未找到 ({style_id})，使用默认")
        tokens = _DEFAULT_STYLE_TOKENS.model_copy(update={"style_id": style_id})
    else:
        tokens = StyleTokens(
            style_id=style_id,
            primary_color=style_data.get("primary_color", _DEFAULT_STYLE_TOKENS.primary_color),
            secondary_color=style_data.get("secondary_color", _DEFAULT_STYLE_TOKENS.secondary_color),
            accent_color=style_data.get("accent_color", _DEFAULT_STYLE_TOKENS.accent_color),
            background_color=style_data.get("background_color", _DEFAULT_STYLE_TOKENS.background_color),
            text_color=style_data.get("text_color", _DEFAULT_STYLE_TOKENS.text_color),
            font_size_base=int(style_data.get("font_size_base", _DEFAULT_STYLE_TOKENS.font_size_base)),
            border_radius=int(style_data.get("border_radius", _DEFAULT_STYLE_TOKENS.border_radius)),
            card_shadow=style_data.get("card_shadow", _DEFAULT_STYLE_TOKENS.card_shadow),
            cover_gradient=style_data.get("cover_gradient", _DEFAULT_STYLE_TOKENS.cover_gradient),
            summary_gradient=style_data.get("summary_gradient", _DEFAULT_STYLE_TOKENS.summary_gradient),
        )

    # 5. 平台微调
    tokens = _apply_platform_tweaks(tokens, router_decision.platform)

    return tokens
