"""
orchestrator/retry_budget.py — 重试预算管理（v3.1.3）

按 PRD 4.11.4–4.11.7：
- render_only：最多 3 次（仅重渲染，不重跑 Agent）
- blueprint_level：最多 2 次（回退到 Agent2 或 Agent3 重跑）
- fact_drift：最多 1 次（OCR 检测到事实漂移，回退到 Worker A 重生成）

高层级触发时：高层级计数器自增，低层级计数器归零（不累计）。
任何层级用完后对应降级（L1/L2/L3）。
"""

from __future__ import annotations

from ir.models import RetryBudgetState


# ── 层级定义 ─────────────────────────────────────────────────────────────────

_LEVEL_PRIORITY = {
    "render_only": 1,         # 低优先级（仅重渲染）
    "blueprint_level": 2,     # 中优先级（重跑蓝图生成）
    "fact_drift": 3,          # 高优先级（事实漂移，重跑文案）
}

# 降级策略标签
_DEGRADATION_LABELS = {
    "render_only": "L1",      # 渲染参数降级
    "blueprint_level": "L2",  # Blueprint 降级（卡片数减少，回到最小预算）
    "fact_drift": "L3",       # 全面降级（text_only 模式，不依赖 LLM 文案）
}


def consume_budget(
    state: RetryBudgetState,
    issue_type: str,
) -> tuple[RetryBudgetState, bool, str]:
    """
    消耗一次重试预算。

    Args:
        state: 当前预算状态（不可变，返回新状态）
        issue_type: "render_only" | "blueprint_level" | "fact_drift"

    Returns:
        (new_state, can_retry, log_msg)
        - new_state: 更新后的预算状态
        - can_retry: True 表示还有预算可以重试
        - log_msg: 日志消息（含计数器快照）
    """
    if issue_type not in _LEVEL_PRIORITY:
        return state, False, f"[RetryBudget] 未知 issue_type: {issue_type}"

    # 检查当前层级是否还有预算
    can = state.can_retry(issue_type)
    if not can:
        label = _DEGRADATION_LABELS.get(issue_type, "L?")
        log_msg = (
            f"[RetryBudget] ❌ {issue_type} 预算耗尽，触发 {label} 降级 | "
            f"计数器: {state.snapshot()}"
        )
        return state, False, log_msg

    # 消耗预算（高层级触发时归零低层级计数器）
    new_state = state.incremented(issue_type)
    priority = _LEVEL_PRIORITY[issue_type]

    # 高层级覆盖低层级：归零低于当前优先级的计数器
    for lower_type, lower_prio in _LEVEL_PRIORITY.items():
        if lower_prio < priority:
            new_state = new_state.model_copy(update={lower_type: 0})

    log_msg = (
        f"[RetryBudget] ⚠️  {issue_type} 消耗预算 | "
        f"计数器: {new_state.snapshot()}"
    )
    return new_state, True, log_msg


def get_degradation_label(state: RetryBudgetState) -> str | None:
    """
    检查是否有任意层级到达上限。返回触发的降级标签（L1/L2/L3），否则返回 None。
    按优先级从高到低检查。
    """
    for issue_type in ["fact_drift", "blueprint_level", "render_only"]:
        if not state.can_retry(issue_type):
            return _DEGRADATION_LABELS[issue_type]
    return None
