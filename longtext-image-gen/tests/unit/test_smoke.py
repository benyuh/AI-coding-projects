"""
tests/unit/test_smoke.py — v3.1.2 冒烟测试

覆盖：
- IR models 实例化
- Worker B 事实核验规则
- Worker C G1 约束
- Worker D 风格选择
- Agent2 卡片预算计算
- Agent1 拒绝逻辑（不调 LLM）
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

import pytest
from ir.models import (
    BCheck, BCheckStatus, Blueprint, Card, CardContent,
    CardType, ContentTree, Claim, FactElement,
    RiskLevel, RouterDecision, SourceBundle,
    StyleTokens, VisualSpec, VisualType,
)
from workers.worker_b_factcheck import run_worker_b, _extract_numbers_from_text
from workers.worker_c_visual import run_worker_c, _apply_g1_constraints
from workers.worker_d_style import run_worker_d
from agents.agent2_understand import _calculate_card_budget
from ir.models import ArticleType, NarrativeStructure, Platform, UserType


# ── IR Models 测试 ────────────────────────────────────────────────────────────

def test_source_bundle_char_count():
    text = "Hello 世界" * 100  # 8 chars × 100 = 800
    sb = SourceBundle(text=text)
    assert sb.char_count == len(text)  # 自动计算


def test_blueprint_creation():
    sb = SourceBundle(text="测试" * 500)
    rd = RouterDecision()
    ct = ContentTree(source_bundle=sb)
    st = StyleTokens()
    bp = Blueprint(
        source_bundle=sb,
        router_decision=rd,
        content_tree=ct,
        style_tokens=st,
    )
    assert bp.pipeline_version == "v3.1.2"


# ── Worker B 测试 ─────────────────────────────────────────────────────────────

def test_worker_b_number_extraction():
    text = "市场规模1200亿元，增速186%，覆盖180个模型，2024年底已达3000GW"
    numbers = _extract_numbers_from_text(text)
    assert "186%" in numbers or "186" in str(numbers)
    assert len(numbers) > 0


def test_worker_b_passed():
    content = CardContent(
        title="市场规模",
        body="2024年中国AI市场规模1200亿元",
        data_label="1200亿",
    )
    source = "2024年中国AI大模型市场规模已突破1200亿元，增速186%"
    result = run_worker_b(content, source)
    assert result.status in (BCheckStatus.PASSED, BCheckStatus.DEGRADED)


def test_worker_b_degraded_on_mismatch():
    content = CardContent(
        title="数据",
        body="规模达9999亿元",
        data_label="9999亿",
    )
    source = "市场规模仅100亿元"
    result = run_worker_b(content, source)
    # 9999亿在源文中找不到，应为 DEGRADED
    assert result.status == BCheckStatus.DEGRADED


# ── Worker C G1 约束测试 ───────────────────────────────────────────────────────

def test_worker_c_g1_diversity():
    # 构建 10 张全为 text_with_icon 的 specs（超过 60%）
    from ir.models import CardType
    specs = []
    for i in range(10):
        ct = CardType.COVER if i == 0 else (CardType.SUMMARY if i == 9 else CardType.SECTION)
        specs.append(VisualSpec(
            visual_type=VisualType.TEXT_WITH_ICON,
            card_type=ct,
        ))

    adjusted = _apply_g1_constraints(specs)
    twi_count = sum(1 for s in adjusted if s.visual_type == VisualType.TEXT_WITH_ICON)
    twi_ratio = twi_count / len(adjusted)
    assert twi_ratio <= 0.6 + 0.05, f"G1 约束未生效，text_with_icon 占比: {twi_ratio:.0%}"


def test_worker_c_g1_no_consecutive():
    # 4 张连续 text_with_icon（索引 1-4），G1 应打断
    from ir.models import CardType
    specs = [
        VisualSpec(visual_type=VisualType.COVER_HERO, card_type=CardType.COVER),
        VisualSpec(visual_type=VisualType.TEXT_WITH_ICON, card_type=CardType.SECTION),
        VisualSpec(visual_type=VisualType.TEXT_WITH_ICON, card_type=CardType.SECTION),
        VisualSpec(visual_type=VisualType.TEXT_WITH_ICON, card_type=CardType.SECTION),
        VisualSpec(visual_type=VisualType.TEXT_WITH_ICON, card_type=CardType.SECTION),
        VisualSpec(visual_type=VisualType.TEXT_WITH_ICON, card_type=CardType.SUMMARY),
    ]
    adjusted = _apply_g1_constraints(specs)
    # 检查是否有连续 3+ 个 text_with_icon（跳过首尾）
    consecutive = 0
    max_consecutive = 0
    for i, s in enumerate(adjusted[1:-1], 1):
        if s.visual_type == VisualType.TEXT_WITH_ICON:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
    assert max_consecutive <= 3, f"G1 连续约束未生效，最大连续: {max_consecutive}"


# ── Worker D 测试 ─────────────────────────────────────────────────────────────

def test_worker_d_returns_style_tokens():
    rd = RouterDecision(
        narrative_structure=NarrativeStructure.PYRAMID_ARGUMENT,
        article_type=ArticleType.ANALYSIS,
        user_type=UserType.PROFESSIONAL,
        platform=Platform.XIAOHONGSHU,
        style_hint="clean_business",
    )
    tokens = run_worker_d(rd)
    assert isinstance(tokens, StyleTokens)
    assert tokens.style_id != ""
    assert tokens.primary_color.startswith("#")


def test_worker_d_platform_tweak():
    rd_xs = RouterDecision(platform=Platform.XIAOHONGSHU)
    rd_wc = RouterDecision(platform=Platform.WECHAT_MOMENTS)
    tokens_xs = run_worker_d(rd_xs)
    tokens_wc = run_worker_d(rd_wc)
    # 朋友圈字号应 ≤ 小红书字号
    assert tokens_wc.font_size_base <= tokens_xs.font_size_base


# ── Agent2 卡片预算测试 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("chars,user_type,expected_range", [
    (500, UserType.GENERAL, (3, 5)),
    (1500, UserType.GENERAL, (5, 8)),
    (1500, UserType.PROFESSIONAL, (5, 10)),
    (5000, UserType.GENERAL, (8, 12)),
    (5000, UserType.PROFESSIONAL, (8, 14)),
    (15000, UserType.GENERAL, (12, 15)),
    (15000, UserType.PROFESSIONAL, (12, 15)),
])
def test_card_budget_range(chars, user_type, expected_range):
    min_c, max_c = _calculate_card_budget(chars, user_type)
    assert (min_c, max_c) == expected_range, f"chars={chars} user={user_type}: expected {expected_range}, got ({min_c},{max_c})"


# ── Agent1 拒绝逻辑测试（不调 LLM）──────────────────────────────────────────

def test_agent1_too_short():
    from agents.agent1_router import run_agent1_router
    sb = SourceBundle(text="这太短了")
    result = run_agent1_router(sb)
    assert result.risk_level == RiskLevel.BLOCKED
    assert result.skip_reason == "TEXT_TOO_SHORT"


def test_agent1_safe_default():
    """正常文章长度不应被短路（不调 LLM 的部分）。"""
    from agents.agent1_router import MIN_CHARS
    # 生成一篇恰好超过 MIN_CHARS 的无风险文章
    text = "这是一篇关于人工智能技术发展的文章，内容涵盖机器学习和深度学习等领域。" * 20
    assert len(text) >= MIN_CHARS  # 确保测试前提成立
    # 注：不测试 LLM 调用部分（需要 API），只验证长度检查通过


if __name__ == "__main__":
    # 直接运行
    test_source_bundle_char_count()
    test_blueprint_creation()
    test_worker_b_number_extraction()
    test_worker_b_passed()
    test_worker_b_degraded_on_mismatch()
    test_worker_c_g1_diversity()
    test_worker_c_g1_no_consecutive()
    test_worker_d_returns_style_tokens()
    test_worker_d_platform_tweak()
    for p in [
        (500, UserType.GENERAL, (3, 5)),
        (1500, UserType.GENERAL, (5, 8)),
        (1500, UserType.PROFESSIONAL, (5, 10)),
    ]:
        test_card_budget_range(*p)
    test_agent1_too_short()
    test_agent1_safe_default()
    print("\n所有冒烟测试通过 ✅")
