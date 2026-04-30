"""
tests/eval/assertions.py — 评估断言库（v3.1.5）
"""

from typing import Any, Dict
from ir.models import RiskLevel, OutputFormat

def assert_functional_pass(case_id: str, state: Dict[str, Any]):
    """
    通用功能测试断言 (§2.3)。
    """
    rd = state.get("router_decision")
    assert rd is not None, f"[{case_id}] Missing router_decision"
    
    risk_level = rd.get("risk_level") if isinstance(rd, dict) else rd.risk_level
    assert risk_level != "blocked" and risk_level != RiskLevel.BLOCKED, f"[{case_id}] Expected safe but got {risk_level}"
    
    blueprint = state.get("blueprint")
    assert blueprint is not None, f"[{case_id}] Missing blueprint"
    cards = blueprint.get("cards") if isinstance(blueprint, dict) else blueprint.cards
    assert 3 <= len(cards) <= 15, f"[{case_id}] Card count {len(cards)} out of range [3, 15]"
    
    g1 = state.get("gate1_result")
    assert g1 is not None, f"[{case_id}] Missing gate1_result"
    passed = g1.get("passed") if isinstance(g1, dict) else g1.passed
    assert passed, f"[{case_id}] Gate 1 failed"
    
    g2 = state.get("gate2_result")
    if g2:
        g2_passed = g2.get("passed") if isinstance(g2, dict) else g2.passed
        assert g2_passed or state.get("degradation_level") == "L1", f"[{case_id}] Gate 2 failed without L1 degradation"

def assert_video_pass(case_id: str, state: Dict[str, Any]):
    """
    视频路径额外断言 (§2.3)。
    """
    artifact = state.get("render_artifact")
    assert artifact is not None, f"[{case_id}] Missing render_artifact"
    atype = artifact.get("artifact_type") if isinstance(artifact, dict) else artifact.artifact_type
    assert atype == "video" or (hasattr(atype, "value") and atype.value == "video"), f"[{case_id}] Expected video but got {atype}"
    
    opath = artifact.get("output_path") if isinstance(artifact, dict) else artifact.output_path
    assert os.path.exists(opath), f"[{case_id}] Video file not found: {opath}"

def assert_edge_behavior(case_id: str, state: Dict[str, Any], expected_behavior: Dict[str, Any]):
    """
    极限测试行为断言 (§3.3)。
    """
    import os
    expected_terminal = expected_behavior.get("expected_terminal")
    
    if expected_terminal == "passed":
        assert_functional_pass(case_id, state)
    
    elif expected_terminal == "rejected_with_alternative":
        rd = state.get("router_decision")
        assert rd is not None, f"[{case_id}] Missing router_decision"
        risk_level = rd.get("risk_level") if isinstance(rd, dict) else rd.risk_level
        assert risk_level == "blocked" or risk_level == RiskLevel.BLOCKED, f"[{case_id}] Expected blocked but got {risk_level}"
        
        risk_reason = rd.get("risk_reason") if isinstance(rd, dict) else rd.risk_reason
        assert risk_reason, f"[{case_id}] Missing risk_reason for rejection"

    elif expected_terminal == "skip_pipeline":
        rd = state.get("router_decision")
        assert rd is not None, f"[{case_id}] Missing router_decision"
        skip_reason = rd.get("skip_reason") if isinstance(rd, dict) else rd.skip_reason
        assert skip_reason is not None, f"[{case_id}] Expected skip_reason but got None"
        
    elif expected_terminal.startswith("degraded_"):
        expected_level = expected_terminal.split("_")[1].upper() # L1, L2, L3
        assert state.get("degradation_level") == expected_level, f"[{case_id}] Expected {expected_level} but got {state.get('degradation_level')}"
