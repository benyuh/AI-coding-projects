"""
tests/unit/test_gate_smoke.py — v3.1.3 Gate 冒烟测试

覆盖：
- RetryBudget 三层计数逻辑
- RetryBudget 高层级归零低层级
- Gate2 字号检查
- Gate2 OCR 降级（非静默）
- LangGraph 图编译
- Gate1 模块可导入（不调 LLM）
"""

import sys
import os
import io
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

import pytest
from ir.models import (
    Blueprint, Card, CardContent, ContentTree, Gate2Issue, Gate2Result,
    RetryBudgetState, RouterDecision, SourceBundle, StyleTokens, VisualSpec,
)
from orchestrator.retry_budget import consume_budget, get_degradation_label
from gates.gate2_render import _check_font_size


# ── RetryBudget 测试 ──────────────────────────────────────────────────────────

def test_retry_budget_initial():
    b = RetryBudgetState()
    assert b.render_only == 0
    assert b.blueprint_level == 0
    assert b.fact_drift == 0
    assert b.can_retry("render_only")
    assert b.can_retry("blueprint_level")
    assert b.can_retry("fact_drift")


def test_retry_budget_render_only_exhaust():
    b = RetryBudgetState()
    for i in range(3):
        b, can, _ = consume_budget(b, "render_only")
        assert can, f"第{i+1}次应该可以重试"
    b, can, _ = consume_budget(b, "render_only")
    assert not can, "第4次应该耗尽"
    assert get_degradation_label(b) == "L1"


def test_retry_budget_blueprint_level_exhaust():
    b = RetryBudgetState()
    b, can, _ = consume_budget(b, "blueprint_level")
    assert can
    b, can, _ = consume_budget(b, "blueprint_level")
    assert can
    b, can, _ = consume_budget(b, "blueprint_level")
    assert not can, "blueprint_level 最多2次，第3次应耗尽"
    assert get_degradation_label(b) == "L2"


def test_retry_budget_fact_drift_exhaust():
    b = RetryBudgetState()
    b, can, _ = consume_budget(b, "fact_drift")
    assert can
    b, can, _ = consume_budget(b, "fact_drift")
    assert not can, "fact_drift 最多1次，第2次应耗尽"
    assert get_degradation_label(b) == "L3"


def test_retry_budget_high_level_resets_low():
    """高层级触发时，低层级计数器应归零。"""
    b = RetryBudgetState()
    b, _, _ = consume_budget(b, "render_only")
    b, _, _ = consume_budget(b, "render_only")
    assert b.render_only == 2

    # 触发 blueprint_level（高于 render_only）
    b, _, _ = consume_budget(b, "blueprint_level")
    assert b.render_only == 0, "高层级触发后，render_only 应归零"
    assert b.blueprint_level == 1


def test_retry_budget_snapshot():
    b = RetryBudgetState(render_only=1, blueprint_level=0, fact_drift=0)
    snap = b.snapshot()
    assert "render_only=1/3" in snap
    assert "blueprint_level=0/2" in snap
    assert "fact_drift=0/1" in snap


# ── Gate2 字号检查 ─────────────────────────────────────────────────────────────

def _make_blueprint(font_size: int) -> Blueprint:
    sb = SourceBundle(text="测试文本" * 100)
    rd = RouterDecision()
    ct = ContentTree(source_bundle=sb)
    st = StyleTokens(font_size_base=font_size)
    return Blueprint(source_bundle=sb, router_decision=rd, content_tree=ct, style_tokens=st)


def test_gate2_font_size_pass():
    bp = _make_blueprint(26)
    issue = _check_font_size(bp)
    assert issue.passed
    assert "≥18px" in issue.detail


def test_gate2_font_size_fail():
    bp = _make_blueprint(14)
    issue = _check_font_size(bp)
    assert not issue.passed
    assert issue.issue_type == "render_only"


def test_gate2_font_size_boundary():
    bp18 = _make_blueprint(18)
    issue = _check_font_size(bp18)
    assert issue.passed, "18px 刚好合格"

    bp17 = _make_blueprint(17)
    issue = _check_font_size(bp17)
    assert not issue.passed, "17px 不合格"


# ── OCR 降级：有明确日志，非静默跳过 ─────────────────────────────────────────

def test_gate2_ocr_degraded_not_silent():
    """OCR 未安装时必须输出警告，不能静默跳过。"""
    from gates.gate2_render import _check_ocr_typo, _OCR_AVAILABLE
    if _OCR_AVAILABLE:
        pytest.skip("OCR 已安装，跳过降级测试")

    bp = _make_blueprint(26)
    # 捕获 print 输出（_check_ocr_typo 用 print 输出警告）
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        issue = _check_ocr_typo("dummy_path.png", bp)
    output = buf.getvalue()

    assert issue.passed, "OCR 未安装时应通过（降级，不阻塞）"
    assert "OCR 未安装" in output, f"必须有明确日志，实际输出: {repr(output)}"


# ── LangGraph 图编译 ──────────────────────────────────────────────────────────

def test_langgraph_compile():
    from orchestrator.graph import build_pipeline_graph
    graph = build_pipeline_graph()
    app = graph.compile()
    nodes = list(app.get_graph().nodes)
    required_nodes = ["agent1", "agent2", "agent3", "gate1", "tool1", "gate2", "done"]
    for node in required_nodes:
        assert node in nodes, f"节点 {node} 缺失，实际节点: {nodes}"


# ── Gate1 模块可导入（不调 LLM）─────────────────────────────────────────────

def test_gate1_import():
    from gates.gate1_blueprint import run_gate1_blueprint, run_clickbait_stage2
    assert callable(run_gate1_blueprint)
    assert callable(run_clickbait_stage2)


if __name__ == "__main__":
    test_retry_budget_initial()
    test_retry_budget_render_only_exhaust()
    test_retry_budget_blueprint_level_exhaust()
    test_retry_budget_fact_drift_exhaust()
    test_retry_budget_high_level_resets_low()
    test_retry_budget_snapshot()
    test_gate2_font_size_pass()
    test_gate2_font_size_fail()
    test_gate2_font_size_boundary()
    test_gate2_ocr_degraded_not_silent()
    test_langgraph_compile()
    test_gate1_import()
    print("\n所有 Gate 冒烟测试通过 ✅")
