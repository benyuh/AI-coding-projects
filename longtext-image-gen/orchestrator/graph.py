"""
orchestrator/graph.py — Orchestrator 主图（v3.1.6）

v3.1.6 改动：
- PipelineState 增加：context_index / target_platform / terminal_status / gate1_retry_payload
- node_agent1 传递 target_platform
- node_agent2 取 ContentTree.context_index → 写入 state
- node_agent3 传 context_index
- Gate1 faithfulness 失败时 failed_card_ids/failed_facts 放入 gate1_retry_payload
- 降级节点设置 terminal_status（l1/l2/l3_degraded）
- blocked 时设置 terminal_status=blocked
- video → image 降级时标记 degrade_format=True / degrade_reason
"""

from __future__ import annotations

import pathlib
import tempfile
import time
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, END

from infra.tracing import trace
from ir.models import (
    Blueprint, ContextIndex, Gate1Result, Gate2Result,
    OutputFormat, RenderArtifact, RetryBudgetState, RiskLevel, SourceBundle,
    TerminalStatus,
)
from agents.agent0_multisource import run_agent0_multisource
from agents.agent1_router import run_agent1_router
from agents.agent2_understand import run_agent2_understand
from agents.agent3_orchestrate import run_agent3_orchestrate
from gates.gate1_blueprint import run_gate1_blueprint
from gates.gate2_render import run_gate2_render
from tools.tool1_image_render import run_tool1_render
from tools.tool2_video_render import run_tool2_video_render
from orchestrator.retry_budget import consume_budget, get_degradation_label


# ── Pipeline 状态定义 ─────────────────────────────────────────────────────────

class PipelineState(TypedDict, total=False):
    """LangGraph 节点间共享的流水线状态。"""
    # 输入
    source_bundle: SourceBundle
    output_path: str
    output_format: OutputFormat
    target_platform: Optional[str]   # 新增：强制路由目标平台

    # 各阶段输出
    router_decision: Any
    content_tree: Any
    context_index: Optional[ContextIndex]   # 新增：ContextIndex 共享
    blueprint: Optional[Blueprint]
    render_artifact: Optional[RenderArtifact]
    gate1_result: Optional[Gate1Result]
    gate2_result: Optional[Gate2Result]

    # 重试控制
    retry_budget: RetryBudgetState
    retry_reason: str
    gate1_retry_payload: Optional[dict]     # 新增：Gate1 faithfulness 定位信息

    # 最终状态
    terminal_status: Optional[str]          # 新增：TerminalStatus 字符串
    degradation_level: str
    error: str
    completed: bool


# ── 节点函数 ──────────────────────────────────────────────────────────────────

def node_agent0(state: PipelineState) -> PipelineState:
    """Agent 0 多信源聚合：单源透传，多源去重/冲突初筛。"""
    sb = run_agent0_multisource(state["source_bundle"])
    return {**state, "source_bundle": sb}


def node_agent1(state: PipelineState) -> PipelineState:
    """Agent 1 Router：风险预检 + 路由决策（含强制 target_platform）。"""
    sb = state["source_bundle"]
    target_platform = state.get("target_platform")
    router_decision = run_agent1_router(sb, target_platform=target_platform)
    output_format = state.get("output_format", OutputFormat.IMAGE)
    if output_format != OutputFormat.AUTO:
        router_decision = router_decision.model_copy(update={"output_format_hint": output_format})
    if router_decision.risk_level == RiskLevel.BLOCKED:
        return {**state, "router_decision": router_decision, "terminal_status": TerminalStatus.BLOCKED.value}
    return {**state, "router_decision": router_decision}


def node_agent2(state: PipelineState) -> PipelineState:
    """Agent 2 内容理解：真分块 + Claim + 卡片预算 + ContextIndex。"""
    rd = state["router_decision"]
    sb = state["source_bundle"]
    content_tree = run_agent2_understand(sb, rd)
    # 从 content_tree 取出 context_index 写入 state
    ctx_idx = content_tree.context_index
    return {**state, "content_tree": content_tree, "context_index": ctx_idx}


def node_agent3(state: PipelineState) -> PipelineState:
    """Agent 3 Orchestrator：Workers A/B/C/D + Blueprint 组装（含 context_index + gate1_retry_payload）。"""
    sb = state["source_bundle"]
    rd = state["router_decision"]
    ct = state["content_tree"]
    ctx_idx = state.get("context_index")
    gate1_retry_payload = state.get("gate1_retry_payload")
    blueprint = run_agent3_orchestrate(sb, rd, ct, context_index=ctx_idx, gate1_retry_payload=gate1_retry_payload)
    output_format = state.get("output_format", rd.output_format_hint)
    if output_format == OutputFormat.AUTO:
        output_format = rd.output_format_hint if rd.output_format_hint != OutputFormat.AUTO else OutputFormat.IMAGE
    blueprint = blueprint.model_copy(update={"output_format": output_format})
    return {**state, "blueprint": blueprint}


def node_gate1(state: PipelineState) -> PipelineState:
    """Gate 1 蓝图评估：4 维 LLM 评分 + 标题党阶段 2。"""
    blueprint = state["blueprint"]
    gate1_result, updated_blueprint = run_gate1_blueprint(blueprint)

    if gate1_result.passed:
        return {**state, "gate1_result": gate1_result, "blueprint": updated_blueprint}

    # Gate1 失败时在节点内消耗预算，路由函数只读不写
    new_budget, _can_retry, log = consume_budget(state["retry_budget"], "blueprint_level")
    print(log)

    # 收集 faithfulness 定位信息
    gate1_retry_payload: Optional[dict] = None
    for ds in gate1_result.dim_scores:
        if ds.dimension == "faithfulness" and not ds.passed and (ds.failed_card_ids or ds.failed_facts):
            gate1_retry_payload = {
                "failed_card_ids": ds.failed_card_ids,
                "failed_facts": [
                    {
                        "card_id": ff.card_id,
                        "card_field": ff.card_field,
                        "original_text": ff.original_text,
                        "hallucinated_text": ff.hallucinated_text,
                        "error_summary": ff.error_summary,
                    }
                    for ff in ds.failed_facts
                ],
            }
            break

    return {
        **state,
        "gate1_result": gate1_result,
        "blueprint": updated_blueprint,
        "retry_budget": new_budget,
        "retry_reason": f"Gate1 失败维度: {gate1_result.failed_dims}",
        "gate1_retry_payload": gate1_retry_payload,
    }


def node_tool1(state: PipelineState) -> PipelineState:
    """Tool 1 图片渲染：Blueprint → PNG。"""
    blueprint = state["blueprint"]
    output_path = state["output_path"]
    artifact = run_tool1_render(blueprint, output_path)
    return {**state, "render_artifact": artifact}


def node_tool2(state: PipelineState) -> PipelineState:
    """Tool 2 视频渲染：Blueprint → MP4。"""
    blueprint = state["blueprint"]
    output_path = state["output_path"]
    try:
        artifact = run_tool2_video_render(blueprint, output_path)
    except Exception as e:
        print(f"[Orchestrator] Tool2 视频生成失败，降级为图片输出: {e}")
        img_output_path = output_path.replace(".mp4", ".png")
        img_artifact = run_tool1_render(
            blueprint.model_copy(update={"output_format": OutputFormat.IMAGE}),
            img_output_path,
        )
        # 标记 degrade_format
        img_artifact = img_artifact.model_copy(update={
            "degrade_format": True,
            "degrade_reason": f"ffmpeg_missing: {e}" if "ffmpeg" in str(e).lower() else str(e),
            "terminal_status": TerminalStatus.L1_DEGRADED,
        })
        return {
            **state,
            "render_artifact": img_artifact,
            "degradation_level": "L1",
            "retry_reason": f"Tool2 failed: {e}",
        }
    return {**state, "render_artifact": artifact}


def node_gate2(state: PipelineState) -> PipelineState:
    """Gate 2 渲染评估：5 项检查。"""
    artifact = state["render_artifact"]
    blueprint = state["blueprint"]
    gate2_result = run_gate2_render(artifact, blueprint)

    if gate2_result.passed:
        return {**state, "gate2_result": gate2_result}

    priority = {"fact_drift": 3, "blueprint_level": 2, "render_only": 1}
    failed_issues = [i for i in gate2_result.issues if not i.passed]
    if not failed_issues:
        return {**state, "gate2_result": gate2_result}

    worst = max(failed_issues, key=lambda i: priority.get(i.issue_type, 0))
    issue_type = worst.issue_type

    new_budget, _can_retry, log = consume_budget(state["retry_budget"], issue_type)
    print(log)
    return {
        **state,
        "gate2_result": gate2_result,
        "retry_budget": new_budget,
        "retry_reason": f"Gate2 失败: {worst.check_name}",
    }


def node_done(state: PipelineState) -> PipelineState:
    """终态节点：标记 Pipeline 完成。"""
    return {**state, "completed": True, "terminal_status": TerminalStatus.PASSED.value}


def node_degrade_l1(state: PipelineState) -> PipelineState:
    """L1 降级：渲染参数降级（减小字号，简化样式），重渲染。"""
    blueprint = state["blueprint"]
    print("[Orchestrator] L1 降级：渲染参数降级")

    st = blueprint.style_tokens
    degraded_tokens = st.model_copy(update={"font_size_base": min(st.font_size_base, 22)})
    degraded_blueprint = blueprint.model_copy(update={"style_tokens": degraded_tokens})

    output_path = state["output_path"]
    if output_path.endswith(".mp4"):
        output_path = output_path[:-4] + ".png"
    try:
        artifact = run_tool1_render(degraded_blueprint, output_path)
        artifact = artifact.model_copy(update={
            "terminal_status": TerminalStatus.L1_DEGRADED,
        })
    except Exception as e:
        print(f"[Orchestrator] L1 降级渲染失败: {e}")
        artifact = state.get("render_artifact")

    return {
        **state,
        "blueprint": degraded_blueprint,
        "render_artifact": artifact,
        "degradation_level": "L1",
        "terminal_status": TerminalStatus.L1_DEGRADED.value,
        "completed": True,
    }


def node_degrade_l2(state: PipelineState) -> PipelineState:
    """L2 降级：Blueprint 降级（卡片数减少到最小预算）。"""
    blueprint = state["blueprint"]
    print("[Orchestrator] L2 降级：Blueprint 卡片数压缩到最小预算")

    ct = blueprint.content_tree
    min_cards = max(3, ct.card_budget // 2)
    cards = blueprint.cards[:min_cards]
    degraded_blueprint = blueprint.model_copy(update={"cards": cards})

    output_path = state["output_path"]
    if output_path.endswith(".mp4"):
        output_path = output_path[:-4] + ".png"
    try:
        artifact = run_tool1_render(degraded_blueprint, output_path)
        artifact = artifact.model_copy(update={
            "terminal_status": TerminalStatus.L2_DEGRADED,
        })
    except Exception as e:
        print(f"[Orchestrator] L2 降级渲染失败: {e}")
        artifact = state.get("render_artifact")

    return {
        **state,
        "blueprint": degraded_blueprint,
        "render_artifact": artifact,
        "degradation_level": "L2",
        "terminal_status": TerminalStatus.L2_DEGRADED.value,
        "completed": True,
    }


def node_degrade_l3(state: PipelineState) -> PipelineState:
    """L3 全面降级：使用简单文本模式。"""
    print("[Orchestrator] L3 全面降级：使用简单文本模式")
    output_path = state["output_path"]
    if output_path.endswith(".mp4"):
        output_path = output_path[:-4] + ".png"
    blueprint = state["blueprint"]
    artifact = state.get("render_artifact")
    if blueprint:
        try:
            artifact = run_tool1_render(blueprint, output_path)
            artifact = artifact.model_copy(update={
                "terminal_status": TerminalStatus.L3_DEGRADED,
            })
        except Exception as e:
            print(f"[Orchestrator] L3 降级渲染失败: {e}")

    return {
        **state,
        "render_artifact": artifact,
        "degradation_level": "L3",
        "terminal_status": TerminalStatus.L3_DEGRADED.value,
        "completed": True,
    }


# ── 路由函数 ──────────────────────────────────────────────────────────────────

def route_after_agent1(state: PipelineState) -> str:
    """Agent1 后：检查是否被 BLOCKED。"""
    rd = state["router_decision"]
    if rd.risk_level == RiskLevel.BLOCKED:
        return "blocked"
    return "agent2"


def route_after_gate1(state: PipelineState) -> str:
    """Gate1 后：通过→tool1，失败→按维度回退或降级。

    预算已由 node_gate1 消耗，此函数只读 state 做决策，不写 state。
    """
    g1 = state["gate1_result"]
    budget = state["retry_budget"]

    if g1.passed:
        blueprint = state.get("blueprint")
        if blueprint and blueprint.output_format == OutputFormat.VIDEO:
            return "tool2"
        return "tool1"

    if not budget.can_retry("blueprint_level"):
        deg_label = get_degradation_level_str(budget)
        return f"degrade_{deg_label.lower()}" if deg_label else "degrade_l2"

    priority_order = ["faithfulness", "completeness", "card_quality", "audience_fit"]
    retry_target = "agent3"
    for dim in priority_order:
        for ds in g1.dim_scores:
            if ds.dimension == dim and not ds.passed:
                retry_target = ds.retry_target
                break
        else:
            continue
        break

    if retry_target == "agent2":
        return "agent2"
    return "agent3"


def route_after_gate2(state: PipelineState) -> str:
    """Gate2 后：通过→done，失败→按 issue_type 回退或降级。

    预算已由 node_gate2 消耗，此函数只读 state 做决策，不写 state。
    """
    g2 = state["gate2_result"]
    budget = state["retry_budget"]

    if g2.passed:
        return "done"

    failed_issues = [i for i in g2.issues if not i.passed]
    if not failed_issues:
        return "done"

    priority = {"fact_drift": 3, "blueprint_level": 2, "render_only": 1}
    worst = max(failed_issues, key=lambda i: priority.get(i.issue_type, 0))
    issue_type = worst.issue_type

    if not budget.can_retry(issue_type):
        deg_label = get_degradation_level_str(budget)
        return f"degrade_{deg_label.lower()}" if deg_label else "degrade_l1"

    if issue_type == "render_only":
        artifact = state.get("render_artifact")
        if artifact and artifact.artifact_type.value == "video":
            return "tool2"
        return "tool1"
    elif issue_type == "blueprint_level":
        return "agent3"
    else:
        return "agent2"


def get_degradation_level_str(budget: RetryBudgetState) -> str:
    """从预算状态获取降级标签。"""
    if not budget.can_retry("fact_drift"):
        return "L3"
    if not budget.can_retry("blueprint_level"):
        return "L2"
    if not budget.can_retry("render_only"):
        return "L1"
    return ""


def route_blocked(_state: PipelineState) -> str:
    return END


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_pipeline_graph() -> StateGraph:
    """构建并返回 Pipeline StateGraph。"""
    graph = StateGraph(PipelineState)

    graph.add_node("agent0", node_agent0)
    graph.add_node("agent1", node_agent1)
    graph.add_node("agent2", node_agent2)
    graph.add_node("agent3", node_agent3)
    graph.add_node("gate1", node_gate1)
    graph.add_node("tool1", node_tool1)
    graph.add_node("tool2", node_tool2)
    graph.add_node("gate2", node_gate2)
    graph.add_node("done", node_done)
    graph.add_node("degrade_l1", node_degrade_l1)
    graph.add_node("degrade_l2", node_degrade_l2)
    graph.add_node("degrade_l3", node_degrade_l3)

    graph.set_entry_point("agent0")

    graph.add_edge("agent0", "agent1")
    graph.add_conditional_edges(
        "agent1",
        route_after_agent1,
        {"agent2": "agent2", "blocked": END},
    )
    graph.add_edge("agent2", "agent3")
    graph.add_edge("agent3", "gate1")
    graph.add_conditional_edges(
        "gate1",
        route_after_gate1,
        {
            "tool1": "tool1",
            "tool2": "tool2",
            "agent2": "agent2",
            "agent3": "agent3",
            "degrade_l1": "degrade_l1",
            "degrade_l2": "degrade_l2",
            "degrade_l3": "degrade_l3",
        },
    )
    graph.add_edge("tool1", "gate2")
    graph.add_edge("tool2", "gate2")
    graph.add_conditional_edges(
        "gate2",
        route_after_gate2,
        {
            "done": "done",
            "tool1": "tool1",
            "tool2": "tool2",
            "agent2": "agent2",
            "agent3": "agent3",
            "degrade_l1": "degrade_l1",
            "degrade_l2": "degrade_l2",
            "degrade_l3": "degrade_l3",
        },
    )
    graph.add_edge("done", END)
    graph.add_edge("degrade_l1", END)
    graph.add_edge("degrade_l2", END)
    graph.add_edge("degrade_l3", END)

    return graph


# ── 主入口 ────────────────────────────────────────────────────────────────────

@trace("Orchestrator.Graph")
def run_pipeline_with_gates(
    text: str | None,
    output_path: str,
    source_bundle: SourceBundle | None = None,
    output_format: OutputFormat = OutputFormat.IMAGE,
    target_platform: Optional[str] = None,
) -> tuple[str, PipelineState]:
    """
    通过 LangGraph 运行完整 Pipeline（含 Gate 1/2 质量闭环）。

    v3.1.6：增加 target_platform 参数（强制路由）。

    Returns:
        (output_path, final_state)
    """
    graph = build_pipeline_graph()
    app = graph.compile()

    initial_state: PipelineState = {
        "source_bundle": source_bundle or SourceBundle(text=text or ""),
        "output_path": output_path,
        "output_format": output_format,
        "target_platform": target_platform,
        "router_decision": None,
        "content_tree": None,
        "context_index": None,
        "blueprint": None,
        "render_artifact": None,
        "gate1_result": None,
        "gate2_result": None,
        "retry_budget": RetryBudgetState(),
        "retry_reason": "",
        "gate1_retry_payload": None,
        "terminal_status": None,
        "degradation_level": "",
        "error": "",
        "completed": False,
    }

    print("[Orchestrator] 启动 Pipeline 图（v3.1.6 事实链路修复）")
    final_state = app.invoke(initial_state)

    artifact = final_state.get("render_artifact")
    if artifact:
        deg = final_state.get("degradation_level", "")
        if deg:
            print(f"[Orchestrator] ⚠️  以 {deg} 降级模式完成")
        return artifact.output_path, final_state

    rd = final_state.get("router_decision")
    if rd and (rd.risk_level == RiskLevel.BLOCKED or rd.skip_reason):
        print(f"[Orchestrator] Pipeline 已按预期停止: risk={rd.risk_level.value}, reason={rd.skip_reason}")
        return "", final_state

    raise RuntimeError("Pipeline 未产生输出产物，流程异常终止")
