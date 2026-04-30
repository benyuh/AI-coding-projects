"""单篇真实长文端到端跑通脚本（阶段 0 验证）。"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from orchestrator.graph import run_pipeline_with_gates
from ir.models import OutputFormat
from infra.tracing import start_trace_capture

INPUT = Path("tests/manual_0429/01_北京义务教育入学政策_0429人工测试.txt")
OUT = Path("evals/runs/PHASE0_smoke/manual_01_policy/artifacts/output.png")
OUT.parent.mkdir(parents=True, exist_ok=True)

text = INPUT.read_text(encoding="utf-8")
print(f"[Phase0] 输入长度: {len(text)} chars")

ctx = start_trace_capture()
t_start = time.time()
output_path, final_state = run_pipeline_with_gates(
    text=text,
    output_path=str(OUT),
    output_format=OutputFormat.IMAGE,
)
elapsed = time.time() - t_start

# 真实性校验
total_in = sum(c.get("tokens_in", 0) for ev in ctx.events for c in ev.model_calls)
total_out = sum(c.get("tokens_out", 0) for ev in ctx.events for c in ev.model_calls)
print(f"\n[Phase0] 端到端耗时: {elapsed:.1f}s")
print(f"[Phase0] sonnet_in={total_in} sonnet_out={total_out}")
print(f"[Phase0] 产物: {output_path}")
print(f"[Phase0] 降级: {final_state.get('degradation_level') or '无'}")

# Gate 评分详情
g1 = final_state.get("gate1_result")
g2 = final_state.get("gate2_result")
if g1:
    print(f"\n[Phase0] Gate1 passed={g1.passed}")
    for ds in g1.dim_scores:
        print(f"  {ds.dimension}: score={ds.score} passed={ds.passed}")
if g2:
    print(f"\n[Phase0] Gate2 passed={g2.passed}")
    for issue in g2.issues:
        print(f"  {issue.check_name}: passed={issue.passed} score={issue.score}")

# Blueprint 摘要
bp = final_state.get("blueprint")
if bp:
    print(f"\n[Phase0] Blueprint: style_id={bp.style_tokens.style_id} cards={len(bp.cards)}")
    rd = final_state.get("router_decision")
    if rd:
        print(f"[Phase0] Router: platform={rd.platform.value} narrative={rd.narrative_structure.value}")

# 保存 result.json 供存档
result_file = OUT.parent.parent / "result.json"
result_data = {
    "elapsed_s": round(elapsed, 2),
    "sonnet_in": total_in,
    "sonnet_out": total_out,
    "output_path": str(output_path),
    "degradation_level": final_state.get("degradation_level", ""),
    "gate1_passed": g1.passed if g1 else None,
    "gate2_passed": g2.passed if g2 else None,
    "style_id": bp.style_tokens.style_id if bp else None,
    "card_count": len(bp.cards) if bp else None,
}
result_file.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n[Phase0] result.json 已保存: {result_file}")

# 硬断言
assert total_in > 0, "❌ token=0，LLM 全部 fallback 了"
assert elapsed >= 15, f"❌ 耗时仅 {elapsed:.1f}s，可疑 fallback"
assert Path(output_path).exists() and Path(output_path).stat().st_size > 50_000, "❌ 产物缺失或过小"
print("\n✅ Phase 0 真实性校验通过")
