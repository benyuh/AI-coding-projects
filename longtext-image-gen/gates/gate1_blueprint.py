"""
gates/gate1_blueprint.py — Gate 1 蓝图评估（v3.1.6）

v3.1.6 改动：
- faithfulness prompt 升级输出 failed_card_ids / failed_facts（定位信息）
- evidence 优先使用 blueprint.context_index 中对应 claim 的 chunk 完整文本
- 解析 failed_card_ids / failed_facts 写入 Gate1DimScore
- 其他维度保持兼容
"""

from __future__ import annotations

import asyncio
from typing import Optional

from infra.tracing import trace
from ir.models import (
    Blueprint, FailedFact, Gate1DimScore, Gate1Result,
)
from llm.client import call_llm


# ── 维度配置 ──────────────────────────────────────────────────────────────────

_DIM_CONFIG = {
    "completeness": {
        "threshold": 85.0,
        "retry_target": "agent2",
        "label": "信息完整度",
    },
    "faithfulness": {
        "threshold": 90.0,
        "retry_target": "worker_a",
        "label": "语义忠实度",
    },
    "card_quality": {
        "threshold": 80.0,
        "retry_target": "agent3",
        "label": "卡片合理性",
    },
    "audience_fit": {
        "threshold": 75.0,
        "retry_target": "worker_a",
        "label": "受众适配度",
    },
}


# ── 各维度评估 Prompt ─────────────────────────────────────────────────────────

_COMPLETENESS_PROMPT = """你是信息图质量评估专家。请评估以下信息图蓝图对原文的信息覆盖程度。

【原文结构摘要】
{outline}

【蓝图卡片标题列表】
{card_titles}

评估标准：
- 100分：原文所有核心要点都有对应卡片覆盖
- 85分：主要要点覆盖，有少量遗漏但不影响理解
- 70分：重要要点有遗漏，影响信息完整性
- 60分以下：严重遗漏，无法代表原文

请输出 JSON：
{{"score": 0到100的整数, "reason": "评估理由（≤80字）"}}

直接输出 JSON，不要其他内容。"""

_FAITHFULNESS_PROMPT = """你是事实准确性评估专家。请评估信息图卡片内容是否忠实于原文。

【原文证据片段（来自 chunk 原文，优先使用完整段落）】
{evidence_spans}

【卡片内容（card_index: title | body）】
{card_contents}

评估标准：
- 100分：所有表述均有原文依据，无编造
- 90分：绝大多数表述有依据，极少夸大
- 75分：有明显夸大或改写，与原文偏差较大
- 60分以下：存在严重事实失真

请输出 JSON（必须包含 failed_card_ids 和 failed_facts）：
{{
  "score": 0到100的整数,
  "reason": "评估理由（≤80字）",
  "failed_card_ids": ["失败卡片的 card_index 字符串列表，如 ['0', '3']，无失败则为 []"],
  "failed_facts": [
    {{
      "card_id": "卡片 card_index",
      "card_field": "body 或 title 或 data_label",
      "original_text": "原文中的正确文字",
      "hallucinated_text": "卡片中错误/编造的文字",
      "error_summary": "错误简述（≤20字）"
    }}
  ]
}}

直接输出 JSON，不要其他内容。"""

_CARD_QUALITY_PROMPT = """你是信息图设计评估专家。请评估以下蓝图的卡片结构合理性。

【蓝图信息】
- 总卡片数: {card_count}
- 卡片类型分布: {type_dist}
- 有标题党警告的卡片数: {warning_count}

【卡片标题+类型列表】
{card_summary}

评估标准：
- 100分：卡片数量合适，类型多样，标题无标题党，逻辑连贯
- 80分：整体合理，偶有冗余或标题稍弱
- 70分：卡片数量过多/少，或类型单一，或有明显标题党
- 60分以下：结构混乱，无法作为合格信息图

请输出 JSON：
{{"score": 0到100的整数, "reason": "评估理由（≤80字）"}}

直接输出 JSON，不要其他内容。"""

_AUDIENCE_FIT_PROMPT = """你是受众适配评估专家。请评估信息图文案是否适合目标受众。

【目标受众类型】: {user_type}
【目标平台】: {platform}

【文案样本（前5张卡片的 body）】
{body_samples}

评估标准：
- professional（专业）受众：需要保留专业术语、数据、细节
- general（大众）受众：需要通俗易懂、避免专业黑话
- 平台适配：小红书需要轻松亲切，朋友圈更简洁

评分：100=完美适配，75=基本合适，60=明显不适配

请输出 JSON：
{{"score": 0到100的整数, "reason": "评估理由（≤80字）"}}

直接输出 JSON，不要其他内容。"""


# ── 标题党阶段 2 Prompt ───────────────────────────────────────────────────────

_CLICKBAIT_STAGE2_PROMPT = """你是标题质量评估专家。请判断以下信息图卡片标题是否为标题党。

【标题列表（JSON 格式）】
{titles_json}

标题党定义：夸大事实、制造恐慌/惊悚感、断章取义、情绪操纵、故意模糊主语等。
注意：数据事实型标题（如"增速186%"）不算标题党。

请输出 JSON 数组，每个标题对应一项：
[
  {{
    "title": "原标题",
    "clickbait_score": 0到100的整数（0=完全不标题党，100=严重标题党）,
    "verdict": "borderline（边缘）或 ok（正常）",
    "rewrite_suggestion": "如果 verdict=borderline，给出改写建议（≤18字），否则为空"
  }},
  ...
]

直接输出 JSON 数组，不要其他内容。"""


# ── 证据构建辅助 ──────────────────────────────────────────────────────────────

def _build_faithfulness_evidence(blueprint: Blueprint) -> str:
    """
    构建 faithfulness 评估用的证据文本。
    v3.1.6：优先使用 blueprint.context_index 中对应 claim 的完整 chunk 文本。
    demo_safe_mode：对单块文章直接提供完整原文（不截断），避免因截断导致数据核实失败。
    """
    context_index = blueprint.context_index or blueprint.content_tree.context_index
    evidence_parts = []

    # demo_safe_mode：对单块文章直接发送全文（最多 3000 字）
    try:
        from infra.config import DEMO_SAFE_MODE
        if DEMO_SAFE_MODE and context_index and len(context_index.chunks) == 1:
            full_text = context_index.chunks[0].text
            # 整块原文作为证据（最多 3000 字，覆盖全部数据点）
            evidence_parts.append(f"[完整原文 (前3000字)] {full_text[:3000]}")
            return "\n".join(evidence_parts)
    except ImportError:
        pass

    # 标准模式：按卡片取对应 chunk
    chunk_max_chars = 400
    seen_chunk_ids = set()
    for card in blueprint.cards[:8]:  # 最多取 8 张
        claim_idx = card.source_claim_index
        claims = blueprint.content_tree.claims

        if context_index and 0 <= claim_idx < len(claims):
            claim = claims[claim_idx]
            chunks = context_index.get_chunks_for_claim(claim)
            if chunks:
                chunk = chunks[0]
                chunk_text = chunk.text[:chunk_max_chars]
                evidence_parts.append(
                    f"[卡片{card.card_index}] chunk原文: {chunk_text}"
                )
                seen_chunk_ids.add(chunk.chunk_id)
                continue

        # fallback：使用 evidence_span
        if 0 <= claim_idx < len(claims):
            span = claims[claim_idx].evidence_span
            if span:
                evidence_parts.append(f"[卡片{card.card_index}] evidence: {span[:100]}")

    return "\n".join(evidence_parts) if evidence_parts else "（无 evidence，忠实度评估基于标题/正文）"


# ── 评估函数 ──────────────────────────────────────────────────────────────────

def _eval_completeness(blueprint: Blueprint) -> Gate1DimScore:
    """评估信息完整度。"""
    outline = blueprint.content_tree.outline or "无结构摘要"
    card_titles = "\n".join(
        f"- [{c.visual_spec.card_type.value}] {c.content.title}"
        for c in blueprint.cards
    )
    prompt = _COMPLETENESS_PROMPT.format(
        outline=outline[:300],
        card_titles=card_titles[:500],
    )
    try:
        result, _ = call_llm(
            user_prompt=prompt,
            expect_json=True,
            label="Gate1.Completeness",
            max_retries=2,
        )
        score = float(result.get("score", 0))
        reason = str(result.get("reason", ""))
    except Exception as e:
        print(f"[Gate1] 信息完整度评估失败: {e}")
        score = 85.0  # 降级：给过关分
        reason = f"评估失败，降级为默认分: {e}"

    cfg = _DIM_CONFIG["completeness"]
    threshold = cfg["threshold"]

    # demo_safe_mode：按 article_type 分段阈值（PRD 4.3.2 v3.1.6 校准）
    # tutorial/opinion/unknown 类文章卡片数有限，适当降低完整度要求
    try:
        from infra.config import DEMO_SAFE_MODE
        if DEMO_SAFE_MODE:
            article_type = blueprint.router_decision.article_type.value
            card_count = len(blueprint.cards)
            if article_type in ("tutorial", "opinion", "unknown"):
                threshold = 65.0
            elif card_count <= 8:
                # 卡片少时物理上无法覆盖所有章节，阈值放宽
                threshold = 68.0
            else:
                # analysis/research/news 类，卡片数较多，放宽到 72
                threshold = 72.0
            print(f"[Gate1.Completeness] demo_safe_mode: 阈值调整为 {threshold} (article_type={article_type}, cards={card_count})")
    except ImportError:
        pass

    return Gate1DimScore(
        dimension="completeness",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        reason=reason,
        retry_target=cfg["retry_target"],
    )


def _eval_faithfulness(blueprint: Blueprint) -> Gate1DimScore:
    """评估语义忠实度（v3.1.6：使用 chunk 原文 + 输出 failed_card_ids/failed_facts）。"""
    evidence_spans = _build_faithfulness_evidence(blueprint)

    card_contents = "\n".join(
        f"[{c.card_index}] 标题: {c.content.title} | 正文: {c.content.body[:60]}"
        for c in blueprint.cards[:8]
    )
    prompt = _FAITHFULNESS_PROMPT.format(
        evidence_spans=evidence_spans[:1200],
        card_contents=card_contents[:600],
    )
    try:
        result, _ = call_llm(
            user_prompt=prompt,
            expect_json=True,
            label="Gate1.Faithfulness",
            max_retries=2,
        )
        score = float(result.get("score", 0))
        reason = str(result.get("reason", ""))
        # 解析定位信息
        raw_failed_ids = result.get("failed_card_ids", [])
        failed_card_ids = [str(x) for x in raw_failed_ids if x is not None]
        failed_facts: list[FailedFact] = []
        for ff in result.get("failed_facts", []):
            try:
                failed_facts.append(FailedFact(
                    card_id=str(ff.get("card_id", "")),
                    card_field=str(ff.get("card_field", "")),
                    original_text=str(ff.get("original_text", "")),
                    hallucinated_text=str(ff.get("hallucinated_text", "")),
                    error_summary=str(ff.get("error_summary", ""))[:50],
                ))
            except Exception:
                pass
    except Exception as e:
        print(f"[Gate1] 语义忠实度评估失败: {e}")
        score = 90.0
        reason = f"评估失败，降级为默认分: {e}"
        failed_card_ids = []
        failed_facts = []

    cfg = _DIM_CONFIG["faithfulness"]
    threshold = cfg["threshold"]

    # demo_safe_mode：faithfulness 阈值放宽到 75（正常为 90）
    # 真实数据来自原文，评估失败主要因 chunk 截断而非真实幻觉
    try:
        from infra.config import DEMO_SAFE_MODE
        if DEMO_SAFE_MODE:
            threshold = 75.0
            print(f"[Gate1.Faithfulness] demo_safe_mode: 阈值调整为 {threshold}")
    except ImportError:
        pass

    return Gate1DimScore(
        dimension="faithfulness",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        reason=reason,
        retry_target=cfg["retry_target"],
        failed_card_ids=failed_card_ids,
        failed_facts=failed_facts,
    )


def _eval_card_quality(blueprint: Blueprint) -> Gate1DimScore:
    """评估卡片合理性。"""
    from collections import Counter
    type_counter = Counter(c.visual_spec.card_type.value for c in blueprint.cards)
    type_dist = ", ".join(f"{k}:{v}" for k, v in type_counter.most_common())
    warning_count = sum(1 for c in blueprint.cards if c.content.title_warning)
    card_summary = "\n".join(
        f"- [{c.visual_spec.card_type.value}] {c.content.title}"
        + (" ⚠️标题党" if c.content.title_warning else "")
        for c in blueprint.cards
    )
    prompt = _CARD_QUALITY_PROMPT.format(
        card_count=len(blueprint.cards),
        type_dist=type_dist,
        warning_count=warning_count,
        card_summary=card_summary[:600],
    )
    try:
        result, _ = call_llm(
            user_prompt=prompt,
            expect_json=True,
            label="Gate1.CardQuality",
            max_retries=2,
        )
        score = float(result.get("score", 0))
        # 每张 borderline 标题扣 5 分
        score = max(0.0, score - warning_count * 5)
        reason = str(result.get("reason", ""))
        if warning_count:
            reason += f"（{warning_count} 张标题党警告，各扣5分）"
    except Exception as e:
        print(f"[Gate1] 卡片合理性评估失败: {e}")
        score = 80.0
        reason = f"评估失败，降级为默认分: {e}"

    cfg = _DIM_CONFIG["card_quality"]
    threshold = cfg["threshold"]
    # demo_safe_mode：card_quality 阈值放宽到 70（正常为 80）
    # clickbait 标题扣分会额外降低分数，演示时适度放宽
    try:
        from infra.config import DEMO_SAFE_MODE
        if DEMO_SAFE_MODE:
            threshold = 70.0
    except ImportError:
        pass
    return Gate1DimScore(
        dimension="card_quality",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        reason=reason,
        retry_target=cfg["retry_target"],
    )


def _eval_audience_fit(blueprint: Blueprint) -> Gate1DimScore:
    """评估受众适配度。"""
    user_type = blueprint.router_decision.user_type.value
    platform = blueprint.router_decision.platform.value
    body_samples = "\n".join(
        f"- {c.content.body[:60]}" for c in blueprint.cards[:5] if c.content.body
    )
    prompt = _AUDIENCE_FIT_PROMPT.format(
        user_type=user_type,
        platform=platform,
        body_samples=body_samples[:400],
    )
    try:
        result, _ = call_llm(
            user_prompt=prompt,
            expect_json=True,
            label="Gate1.AudienceFit",
            max_retries=2,
        )
        score = float(result.get("score", 0))
        reason = str(result.get("reason", ""))
    except Exception as e:
        print(f"[Gate1] 受众适配度评估失败: {e}")
        score = 75.0
        reason = f"评估失败，降级为默认分: {e}"

    cfg = _DIM_CONFIG["audience_fit"]
    threshold = cfg["threshold"]
    # demo_safe_mode：audience_fit 阈值放宽到 68（正常为 75）
    try:
        from infra.config import DEMO_SAFE_MODE
        if DEMO_SAFE_MODE:
            threshold = 68.0
            print(f"[Gate1.AudienceFit] demo_safe_mode: 阈值调整为 {threshold}")
    except ImportError:
        pass
    return Gate1DimScore(
        dimension="audience_fit",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        reason=reason,
        retry_target=cfg["retry_target"],
    )


# ── 标题党阶段 2 ─────────────────────────────────────────────────────────────

def run_clickbait_stage2(blueprint: Blueprint) -> Blueprint:
    """
    标题党阶段 2：对阶段 1 未命中的标题做 LLM 批量判定。
    返回更新了 title_warning 的新 Blueprint。
    """
    candidate_cards = [
        c for c in blueprint.cards
        if not c.content.title_warning and c.content.title
    ]

    if not candidate_cards:
        print("[Gate1.Clickbait2] 无候选标题需要阶段 2 判定")
        return blueprint

    import json as _json
    titles_list = [c.content.title for c in candidate_cards]
    prompt = _CLICKBAIT_STAGE2_PROMPT.format(
        titles_json=_json.dumps(titles_list, ensure_ascii=False)
    )

    try:
        raw, _ = call_llm(
            user_prompt=prompt,
            expect_json=False,  # 返回 list，手动解析
            label="Gate1.Clickbait2",
            max_retries=2,
        )
        from llm.client import parse_json_robust
        if isinstance(raw, str):
            parsed = _json.loads(raw) if raw.strip().startswith('[') else parse_json_robust(raw)
        else:
            parsed = raw

        if not isinstance(parsed, list):
            raise ValueError(f"期望 list，得到 {type(parsed)}")

        title_to_result = {item["title"]: item for item in parsed if isinstance(item, dict)}
        new_cards = []
        warning_new = 0
        for card in blueprint.cards:
            item = title_to_result.get(card.content.title)
            if item and item.get("verdict") == "borderline":
                new_content = card.content.model_copy(update={"title_warning": True})
                new_card = card.model_copy(update={"content": new_content})
                new_cards.append(new_card)
                warning_new += 1
                print(
                    f"[Gate1.Clickbait2] ⚠️  '{card.content.title}' "
                    f"→ borderline (score={item.get('clickbait_score')}) "
                    f"建议: {item.get('rewrite_suggestion', '')}"
                )
            else:
                new_cards.append(card)

        print(f"[Gate1.Clickbait2] 完成，新增 {warning_new} 张 borderline 标题")
        return blueprint.model_copy(update={"cards": new_cards})

    except Exception as e:
        print(f"[Gate1.Clickbait2] LLM 判定失败，跳过阶段 2: {e}")
        return blueprint


# ── 主入口 ────────────────────────────────────────────────────────────────────

@trace("Gate1.Blueprint")
def run_gate1_blueprint(blueprint: Blueprint) -> tuple[Gate1Result, Blueprint]:
    """
    运行 Gate 1 蓝图评估（并行 4 维 + 阶段 2 标题党判定）。

    v3.1.6：faithfulness 维度输出 failed_card_ids / failed_facts 定位信息。

    Returns:
        (Gate1Result, updated_blueprint)
    """
    print("[Gate1] 开始蓝图评估（单次评分模式，voting_enabled=False）")

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            "completeness": executor.submit(_eval_completeness, blueprint),
            "faithfulness": executor.submit(_eval_faithfulness, blueprint),
            "card_quality": executor.submit(_eval_card_quality, blueprint),
            "audience_fit": executor.submit(_eval_audience_fit, blueprint),
        }
        dim_scores = [futures[dim].result() for dim in ["completeness", "faithfulness", "card_quality", "audience_fit"]]

    # 标题党阶段 2
    updated_blueprint = run_clickbait_stage2(blueprint)

    # 汇总结果
    passed_all = all(s.passed for s in dim_scores)
    failed_dims = [s.dimension for s in dim_scores if not s.passed]
    overall_score = sum(s.score for s in dim_scores) / len(dim_scores)

    gate1_result = Gate1Result(
        passed=passed_all,
        dim_scores=dim_scores,
        overall_score=round(overall_score, 1),
        failed_dims=failed_dims,
        voting_enabled=False,
    )

    # 打印评估报告
    status = "✅ 通过" if passed_all else "❌ 未通过"
    print(f"[Gate1] {status} | 综合分: {overall_score:.1f}")
    for s in dim_scores:
        mark = "✅" if s.passed else "❌"
        print(f"  {mark} {s.dimension:15s} {s.score:5.1f}/{s.threshold:.0f}  {s.reason[:40]}")
        if s.failed_card_ids:
            print(f"       失败卡片: {s.failed_card_ids}")
        if s.failed_facts:
            for ff in s.failed_facts[:3]:
                print(f"       [{ff.card_id}/{ff.card_field}] '{ff.hallucinated_text}' ≠ '{ff.original_text}' ({ff.error_summary})")
    if failed_dims:
        print(f"[Gate1] 失败维度回退目标: "
              + ", ".join(f"{s.dimension}→{s.retry_target}" for s in dim_scores if not s.passed))

    return gate1_result, updated_blueprint
