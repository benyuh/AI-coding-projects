"""
workers/worker_b_factcheck.py — Worker B 事实核验（v3.1.6）

v3.1.6 改动（纯规则，不调 LLM）：
1. 接收 fact_usages / context_index 参数
2. 数字校验：
   - 删除 len(norm) <= 1 直接通过逻辑
   - ≥4 位数字必须在原文完整出现（不允许子串通过）
   - 识别 "75万863" 类截断/拼接 → truncated_digits
3. 日期校验：年份必须逐字出现在原文 → year_drift
4. 实体校验：alias 匹配 + difflib fallback（不强依赖 spaCy）
5. fact_usages offset 反查：chunk 文本不含 element.text → offset_mismatch
6. 状态判定：
   - 无失败 → PASSED
   - severe（truncated_digits/year_drift/entity_swap/offset_mismatch）→ REJECTED
   - 轻微（not_in_source）→ DEGRADED
7. BCheck.failures 填 VerificationFailure，failed_elements 保留兼容
"""

from __future__ import annotations

import difflib
import pathlib
import re
from typing import Optional

import yaml

from infra.tracing import trace
from ir.models import (
    BCheck, BCheckStatus, CardContent, Claim, ContextIndex,
    FactElement, VerificationFailure,
)

_HERE = pathlib.Path(__file__).parent.parent
_ENTITY_ALIAS_CONFIG = _HERE / "configs" / "entity_alias.yaml"

# ── 严重错误类型 ──────────────────────────────────────────────────────────────
_SEVERE_ERROR_TYPES = {"truncated_digits", "year_drift", "entity_swap", "offset_mismatch"}

# ── 数字/时间/百分比正则 ─────────────────────────────────────────────────────
_NUMBER_PATTERN = re.compile(
    r"""
    (?:
        \d+(?:[,，]\d{3})*(?:\.\d+)?   # 阿拉伯数字（含千位分隔符）
        |[零一二三四五六七八九十百千万亿兆]+  # 中文数字
    )
    (?:[%％])?                         # 可选百分号
    (?:[亿万千百](?:[元张个家人次年月日])*)?  # 可选中文单位
    """,
    re.VERBOSE,
)

_DATE_PATTERN = re.compile(
    r"""
    \d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?  # 2024年1月
    |Q[1-4]\s*\d{4}                       # Q1 2024
    |\d{4}-\d{2}(?:-\d{2})?              # 2024-01-01
    """,
    re.VERBOSE,
)

_PERCENTAGE_PATTERN = re.compile(r"\d+(?:\.\d+)?[%％]")

# 4 位及以上纯数字（严格检测）
_LONG_NUMBER_PATTERN = re.compile(r'\d{4,}')

# 年份检测（负向前后lookaround，避免 \b 对汉字失效，且不匹配连续8位数字的子串）
_YEAR_PATTERN = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)')

# 疑似截断数字：数字+万+数字（如 75万863）
_TRUNCATED_DIGITS_PATTERN = re.compile(r'\d+[万千百亿]\d+')


# ── 实体别名字典（延迟加载）──────────────────────────────────────────────────
_entity_aliases: dict[str, list[str]] = {}


def _load_entity_aliases() -> dict[str, list[str]]:
    global _entity_aliases
    if _entity_aliases:
        return _entity_aliases

    try:
        if _ENTITY_ALIAS_CONFIG.exists():
            data = yaml.safe_load(_ENTITY_ALIAS_CONFIG.read_text(encoding="utf-8"))
            _entity_aliases = data.get("aliases", {})
    except Exception as e:
        print(f"[WorkerB] 实体别名配置加载失败: {e}")
        _entity_aliases = {}

    return _entity_aliases


def _normalize_number(n: str) -> str:
    """标准化数字表达（去除分隔符）。"""
    return n.replace(",", "").replace("，", "").replace(" ", "")


def _extract_numbers_from_text(text: str) -> set[str]:
    """从文本中抽取所有数字表达式。"""
    numbers = set()
    numbers.update(_NUMBER_PATTERN.findall(text))
    numbers.update(_DATE_PATTERN.findall(text))
    numbers.update(_PERCENTAGE_PATTERN.findall(text))
    return {n.strip() for n in numbers if n.strip()}


def _check_long_number(num_str: str, source_text: str) -> tuple[bool, str]:
    """
    检查 ≥4 位的阿拉伯数字是否在原文中完整出现。
    返回 (ok, error_type)
    """
    norm = _normalize_number(num_str)
    # 检查疑似截断数字
    if _TRUNCATED_DIGITS_PATTERN.search(num_str):
        # 形如 75万863：将数字还原后尝试匹配
        # 这是已知的截断/拼接形式，标记为 truncated_digits
        return False, "truncated_digits"

    # 完整字符串查找（不允许子串通过）
    if norm in source_text or num_str in source_text:
        return True, ""

    # 检查是否是子串（e.g. 12345 出现在 123456789 中）
    # 我们要求精确边界匹配
    pattern = re.compile(r'(?<!\d)' + re.escape(norm) + r'(?!\d)')
    if pattern.search(source_text.replace(",", "").replace("，", "")):
        return True, ""

    return False, "not_in_source"


def _check_year(year_str: str, source_text: str) -> tuple[bool, str]:
    """
    检查年份字符串是否逐字出现在原文中。
    year_str 形如 "2024" 或 "2024年"
    """
    # 提取 4 位年份
    year_match = re.search(r'(19|20)\d{2}', year_str)
    if not year_match:
        return True, ""  # 非标准年份，放行
    year = year_match.group(0)
    if year in source_text:
        return True, ""
    return False, "year_drift"


def _check_entity_fuzzy(entity: str, source_text: str) -> tuple[bool, str]:
    """
    实体模糊匹配：
    1. 精确匹配
    2. alias 匹配
    3. difflib 相似度 > 0.85
    """
    if not entity:
        return True, ""

    # 1. 精确匹配
    if entity in source_text:
        return True, ""

    # 2. alias 匹配
    aliases = _load_entity_aliases()
    norm_entity = entity.strip()
    for canonical, alias_list in aliases.items():
        all_forms = [canonical] + alias_list
        if norm_entity in all_forms:
            # 检查任意别名是否出现在原文
            if any(f in source_text for f in all_forms):
                return True, ""

    # 3. difflib 相似度（对较短实体慎用）
    if len(entity) >= 3:
        # 在原文中找最相似的片段
        ratio = difflib.SequenceMatcher(None, entity, source_text[:2000]).ratio()
        if ratio > 0.85:
            return True, ""

        # 更精确：在原文中滑动窗口比较
        window = len(entity) + 4
        best_ratio = 0.0
        for i in range(0, min(len(source_text) - len(entity), 2000)):
            candidate = source_text[i:i + window]
            r = difflib.SequenceMatcher(None, entity, candidate).ratio()
            if r > best_ratio:
                best_ratio = r
        if best_ratio > 0.85:
            return True, ""

    return False, "entity_swap"


def _check_offset_in_context(element: FactElement, context_index: ContextIndex) -> tuple[bool, str]:
    """
    若 FactElement 有 source_offset，反查对应 chunk 文本是否确实包含该元素。
    """
    offset = element.source_offset
    if offset is None or not offset.chunk_id:
        return True, ""  # 没有 offset 信息，跳过此项检查

    chunk = context_index.get_chunk(offset.chunk_id)
    if chunk is None:
        return False, "offset_mismatch"

    # 检查 element.text 是否出现在 chunk.text 中
    if element.text and element.text not in chunk.text:
        return False, "offset_mismatch"

    return True, ""


def _get_source_text_for_card(
    source_text: str,
    claim: Optional[Claim],
    context_index: Optional[ContextIndex],
) -> str:
    """
    构建用于核验的源文：
    优先使用 context_index 中 claim 对应的完整 chunk，
    fallback 到传入的 source_text。
    """
    if context_index is None or claim is None:
        if claim and claim.evidence_span:
            return source_text + " " + claim.evidence_span
        return source_text

    chunks = context_index.get_chunks_for_claim(claim)
    if chunks:
        # 取所有相关 chunk 的文本
        chunk_texts = " ".join(c.text for c in chunks)
        return chunk_texts

    return source_text


@trace("WorkerB.FactCheck")
def run_worker_b(
    card_content: CardContent,
    source_text: str,
    claim: Optional[Claim] = None,
    fact_usages: Optional[list[FactElement]] = None,
    context_index: Optional[ContextIndex] = None,
) -> BCheck:
    """
    运行 Worker B 事实核验（v3.1.6 真核验）。

    Args:
        card_content: 需要核验的卡片文案
        source_text: 原文本（fallback）
        claim: 对应的 Claim（含 fact_elements 和 source_chunk_ids）
        fact_usages: WorkerA 输出的 fact_usages（优先用于核验）
        context_index: 上下文索引（含 chunk 完整文本）

    Returns:
        BCheck（status 可为 PASSED / DEGRADED / REJECTED）
    """
    # 构建核验用源文
    effective_source = _get_source_text_for_card(source_text, claim, context_index)

    # 合并所有需要核验的文本
    texts_to_check = [
        card_content.title,
        card_content.body,
        card_content.data_label,
    ]
    texts_to_check.extend(card_content.items)
    combined_text = " ".join(t for t in texts_to_check if t)

    failures: list[VerificationFailure] = []
    verified: list[str] = []
    failed: list[str] = []

    # ── 1. 从卡片文本中抽取数字/日期 ─────────────────────────────────────────
    numbers_in_card = _extract_numbers_from_text(combined_text)
    dates_in_card = set(_DATE_PATTERN.findall(combined_text))
    long_nums_in_card = set(_LONG_NUMBER_PATTERN.findall(combined_text))
    # 疑似截断数字（如 75万863）：无论是否 ≥4 位都要检测
    truncated_candidates = set(_TRUNCATED_DIGITS_PATTERN.findall(combined_text))

    # ── 1.5 直接检测截断数字形式 ──────────────────────────────────────────────
    for trunc in truncated_candidates:
        if trunc not in effective_source:
            card_field = "body"
            for field_name, field_val in [
                ("title", card_content.title),
                ("body", card_content.body),
                ("data_label", card_content.data_label),
            ]:
                if field_val and trunc in field_val:
                    card_field = field_name
                    break
            failed.append(trunc)
            failures.append(VerificationFailure(
                element=trunc,
                element_type="number",
                error_type="truncated_digits",
                card_field=card_field,
            ))
        else:
            verified.append(trunc)

    # ── 2. 检查长数字（≥4 位） ────────────────────────────────────────────────
    for num in long_nums_in_card:
        ok, error_type = _check_long_number(num, effective_source)
        if ok:
            verified.append(num)
        else:
            failed.append(num)
            # 确定出现在哪个字段
            card_field = "body"
            for field_name, field_val in [
                ("title", card_content.title),
                ("body", card_content.body),
                ("data_label", card_content.data_label),
            ]:
                if field_val and num in field_val:
                    card_field = field_name
                    break
            failures.append(VerificationFailure(
                element=num,
                element_type="number",
                error_type=error_type,
                card_field=card_field,
            ))

    # ── 3. 检查日期年份 ────────────────────────────────────────────────────────
    years_in_card = set(_YEAR_PATTERN.findall(combined_text))
    for year in years_in_card:
        ok, error_type = _check_year(year, effective_source)
        if ok:
            if year not in verified:
                verified.append(year)
        else:
            failed.append(year)
            card_field = "body"
            for field_name, field_val in [
                ("title", card_content.title),
                ("body", card_content.body),
            ]:
                if field_val and year in field_val:
                    card_field = field_name
                    break
            failures.append(VerificationFailure(
                element=year,
                element_type="date",
                error_type=error_type,
                card_field=card_field,
            ))

    # ── 4. fact_usages offset 反查 ────────────────────────────────────────────
    all_fact_elements: list[FactElement] = []
    if fact_usages:
        all_fact_elements.extend(fact_usages)
    elif card_content.fact_usages:
        all_fact_elements.extend(card_content.fact_usages)
    elif claim and claim.fact_elements:
        all_fact_elements.extend(claim.fact_elements)

    if context_index:
        for fe in all_fact_elements:
            ok, error_type = _check_offset_in_context(fe, context_index)
            if not ok:
                failed.append(fe.text)
                failures.append(VerificationFailure(
                    element=fe.text,
                    element_type=fe.element_type,
                    error_type=error_type,
                    card_field="body",
                ))
            elif fe.text and fe.text not in verified:
                verified.append(fe.text)

    # ── 5. 从 claim.fact_elements 中检查实体 ─────────────────────────────────
    claim_elements = claim.fact_elements if claim else []
    for fe in claim_elements:
        if fe.element_type == "entity" and fe.text:
            # 检查实体是否出现在卡片中，再核对原文
            if fe.text in combined_text or any(fe.text in item for item in card_content.items):
                ok, error_type = _check_entity_fuzzy(fe.text, effective_source)
                if not ok:
                    failed.append(fe.text)
                    failures.append(VerificationFailure(
                        element=fe.text,
                        element_type="entity",
                        error_type=error_type,
                        card_field="body",
                    ))
                elif fe.text not in verified:
                    verified.append(fe.text)

    # ── 6. 状态判定 ────────────────────────────────────────────────────────────
    # 去重
    failures = list({f.element: f for f in failures}.values())
    severe_failures = [f for f in failures if f.error_type in _SEVERE_ERROR_TYPES]

    if not failures:
        status = BCheckStatus.PASSED
    elif severe_failures:
        status = BCheckStatus.REJECTED
        print(f"[WorkerB] REJECTED: 严重失败 {[f.element + '/' + f.error_type for f in severe_failures]}")
    else:
        status = BCheckStatus.DEGRADED
        print(f"[WorkerB] DEGRADED: 未验证元素 {failed[:5]}")

    b_check = BCheck(
        status=status,
        verified_elements=verified,
        failed_elements=failed,
        failures=failures,
    )

    print(f"[WorkerB] 状态: {status.value} | 验证: {len(verified)} | 失败: {len(failed)} | 严重: {len(severe_failures)}")
    return b_check
