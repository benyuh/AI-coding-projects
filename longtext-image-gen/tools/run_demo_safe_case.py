"""
tools/run_demo_safe_case.py — 演示保底运行脚本（v3.1.6）

用法：
  python3.11 tools/run_demo_safe_case.py [txt_path] [target_platform]

示例：
  python3.11 tools/run_demo_safe_case.py evals/datasets/comprehensive/comp_R03.txt
  python3.11 tools/run_demo_safe_case.py evals/datasets/comprehensive/comp_R02.txt wechat_moments
  python3.11 tools/run_demo_safe_case.py evals/datasets/functional/func_07_multi_source.txt xiaohongshu

自动设置 LONGTEXT_DEMO_SAFE_MODE=1（不需要手动 export）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 自动开启 demo_safe_mode（最先设置，在任何 import 之前）
os.environ["LONGTEXT_DEMO_SAFE_MODE"] = "1"

# 添加项目根目录到 sys.path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from orchestrator.graph import run_pipeline_with_gates
from ir.models import OutputFormat, SourceBundle, Source, SourceMode
from infra.tracing import start_trace_capture


def _load_input(txt_path: Path) -> tuple[str | None, list[str], SourceBundle | None]:
    """
    读取输入文件。支持单源（.txt）和多源（_s1.txt/_s2.txt）。
    返回 (text, multi_sources, source_bundle_or_None)
    """
    if not txt_path.exists():
        # 尝试多源
        base = txt_path.with_suffix("")
        source_files = sorted(base.parent.glob(f"{base.name}_s*.txt"))
        if source_files:
            multi_sources = [sf.read_text(encoding="utf-8") for sf in source_files]
            print(f"[Demo] 多源模式：发现 {len(multi_sources)} 个信源文件")
            sb = SourceBundle(
                mode=SourceMode.MULTI,
                sources=[Source(source_id=f"s{i+1}", raw_text=t) for i, t in enumerate(multi_sources)],
            )
            return None, multi_sources, sb
        raise FileNotFoundError(f"找不到输入文件: {txt_path}")
    text = txt_path.read_text(encoding="utf-8")
    return text, [], None


def main():
    # ── 解析参数 ─────────────────────────────────────────────────────────────
    if len(sys.argv) >= 2:
        txt_path = Path(sys.argv[1])
        if not txt_path.is_absolute():
            txt_path = _ROOT / txt_path
    else:
        # 默认使用 comp_R03（数字截断案例）
        txt_path = _ROOT / "evals/datasets/comprehensive/comp_R03.txt"

    target_platform: str | None = sys.argv[2] if len(sys.argv) >= 3 else None

    print(f"\n{'='*60}")
    print(f"  longtext v3.1.6 演示保底脚本")
    print(f"  LONGTEXT_DEMO_SAFE_MODE = {os.environ.get('LONGTEXT_DEMO_SAFE_MODE')}")
    print(f"  输入: {txt_path}")
    print(f"  target_platform: {target_platform or '(auto)'}")
    print(f"{'='*60}\n")

    # ── 加载输入 ─────────────────────────────────────────────────────────────
    text, multi_sources, source_bundle = _load_input(txt_path)

    if text:
        print(f"[Demo] 输入长度: {len(text)} 字")
    elif multi_sources:
        print(f"[Demo] 多源总长度: {sum(len(t) for t in multi_sources)} 字")

    # ── 准备输出目录 ─────────────────────────────────────────────────────────
    case_name = txt_path.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _ROOT / "evals" / "runs" / f"demo_safe_{ts}_{case_name}"
    artifacts_dir = run_dir / "artifacts"
    stages_dir = run_dir / "stages"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stages_dir.mkdir(parents=True, exist_ok=True)

    output_path = str(artifacts_dir / "output.png")

    # ── 保存 input.json ───────────────────────────────────────────────────────
    input_data = {
        "txt_path": str(txt_path),
        "text_length": len(text) if text else sum(len(t) for t in multi_sources),
        "multi_source_count": len(multi_sources),
        "target_platform": target_platform,
        "demo_safe_mode": True,
    }
    (run_dir / "input.json").write_text(
        json.dumps(input_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── 运行 Pipeline ─────────────────────────────────────────────────────────
    trace_ctx = start_trace_capture()
    t_start = time.time()

    try:
        if source_bundle:
            final_output_path, final_state = run_pipeline_with_gates(
                text=None,
                source_bundle=source_bundle,
                output_path=output_path,
                output_format=OutputFormat.IMAGE,
                target_platform=target_platform,
            )
        else:
            final_output_path, final_state = run_pipeline_with_gates(
                text=text,
                output_path=output_path,
                output_format=OutputFormat.IMAGE,
                target_platform=target_platform,
            )
    except Exception as e:
        elapsed = time.time() - t_start
        print(f"\n❌ Pipeline 异常: {e}")
        import traceback
        traceback.print_exc()
        (run_dir / "error.txt").write_text(
            str(e) + "\n" + traceback.format_exc(), encoding="utf-8"
        )
        print(f"[Demo] 耗时: {elapsed:.1f}s | 错误已保存到: {run_dir}/error.txt")
        sys.exit(1)

    elapsed = time.time() - t_start

    # ── 提取关键信息 ─────────────────────────────────────────────────────────
    total_in = sum(c.get("tokens_in", 0) for ev in trace_ctx.events for c in ev.model_calls)
    total_out = sum(c.get("tokens_out", 0) for ev in trace_ctx.events for c in ev.model_calls)

    terminal_status = final_state.get("terminal_status") or "passed"
    degradation_level = final_state.get("degradation_level", "") or "无"
    bp = final_state.get("blueprint")
    rd = final_state.get("router_decision")
    g1 = final_state.get("gate1_result")
    g2 = final_state.get("gate2_result")

    card_count = len(bp.cards) if bp else 0
    platform = rd.platform.value if rd else "unknown"
    style_id = bp.style_tokens.style_id if bp else "unknown"

    # Gate1 分数
    g1_faithfulness = g1_completeness = g1_card_quality = g1_audience_fit = None
    g1_passed = g1_failed_dims = None
    if g1:
        g1_passed = g1.passed
        g1_failed_dims = g1.failed_dims
        for ds in g1.dim_scores:
            if ds.dimension == "faithfulness":
                g1_faithfulness = (ds.score, ds.passed, ds.reason[:50])
            elif ds.dimension == "completeness":
                g1_completeness = (ds.score, ds.threshold, ds.passed, ds.reason[:50])
            elif ds.dimension == "card_quality":
                g1_card_quality = (ds.score, ds.passed)
            elif ds.dimension == "audience_fit":
                g1_audience_fit = (ds.score, ds.passed)

    # WorkerB 统计
    rejected_cards = []
    if bp:
        for c in bp.cards:
            if c.b_check.status.value == "rejected":
                rejected_cards.append(c.card_index)

    # Gate2 分数
    g2_passed = None
    if g2:
        g2_passed = g2.passed

    # ── 打印结果 ─────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  演示保底运行结果 — {case_name}")
    print(f"{'='*60}")
    print(f"  耗时:           {elapsed:.1f}s")
    print(f"  tokens_in:      {total_in}")
    print(f"  terminal_status:{terminal_status}")
    print(f"  degradation:    {degradation_level}")
    print(f"  platform:       {platform}")
    print(f"  style_id:       {style_id}")
    print(f"  card_count:     {card_count}")
    print(f"  output_path:    {final_output_path}")
    print()
    if g1:
        print(f"  Gate1 passed:   {g1_passed}  (失败维度: {g1_failed_dims})")
        if g1_faithfulness:
            print(f"    faithfulness: {g1_faithfulness[0]:.1f} passed={g1_faithfulness[1]}  {g1_faithfulness[2]}")
        if g1_completeness:
            print(f"    completeness: {g1_completeness[0]:.1f}/{g1_completeness[1]:.0f} passed={g1_completeness[2]}  {g1_completeness[3]}")
        if g1_card_quality:
            print(f"    card_quality: {g1_card_quality[0]:.1f} passed={g1_card_quality[1]}")
        if g1_audience_fit:
            print(f"    audience_fit: {g1_audience_fit[0]:.1f} passed={g1_audience_fit[1]}")
    if g2:
        print(f"  Gate2 passed:   {g2_passed}")
    if rejected_cards:
        print(f"  WorkerB REJECTED 卡片: {rejected_cards}")
    else:
        print(f"  WorkerB REJECTED:  无")
    print(f"{'='*60}\n")

    # ── 保存归档文件 ─────────────────────────────────────────────────────────
    result_data = {
        "terminal_status": terminal_status,
        "degradation_level": degradation_level if degradation_level != "无" else "",
        "elapsed_s": round(elapsed, 2),
        "tokens_in": total_in,
        "tokens_out": total_out,
        "output_path": str(final_output_path),
        "platform": platform,
        "style_id": style_id,
        "card_count": card_count,
        "gate1_passed": g1_passed,
        "gate1_failed_dims": g1_failed_dims,
        "gate1_faithfulness": g1_faithfulness[0] if g1_faithfulness else None,
        "gate1_completeness": g1_completeness[0] if g1_completeness else None,
        "gate2_passed": g2_passed,
        "workerb_rejected_cards": rejected_cards,
        "demo_safe_mode": True,
    }
    (run_dir / "result.json").write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 保存 blueprint.json
    if bp and hasattr(bp, "model_dump_json"):
        (run_dir / "blueprint.json").write_text(
            bp.model_dump_json(indent=2), encoding="utf-8"
        )

    # 保存 stages/
    for key, fname in [
        ("router_decision", "agent1_router.json"),
        ("content_tree", "agent2_understanding.json"),
        ("gate1_result", "gate1.json"),
        ("gate2_result", "gate2.json"),
        ("render_artifact", "tool_render.json"),
    ]:
        data = final_state.get(key)
        if data and hasattr(data, "model_dump_json"):
            (stages_dir / fname).write_text(data.model_dump_json(indent=2), encoding="utf-8")

    print(f"[Demo] 归档目录: {run_dir}")
    print(f"[Demo] blueprint:  {run_dir}/blueprint.json")
    print(f"[Demo] stages:     {stages_dir}/")

    # ── 真实性断言 ────────────────────────────────────────────────────────────
    if total_in == 0:
        print("⚠️  WARNING: token_in=0，LLM 可能全部 fallback！")
    else:
        print(f"✅ LLM 已调通 (tokens_in={total_in})")

    output_file = Path(final_output_path) if final_output_path else None
    if output_file and output_file.exists() and output_file.stat().st_size > 10_000:
        print(f"✅ 输出 PNG 存在 ({output_file.stat().st_size // 1024} KB)")
    else:
        print(f"⚠️  WARNING: 输出 PNG 缺失或过小: {final_output_path}")

    if terminal_status == "passed":
        print("\n🎉 演示保底成功：terminal_status=passed，无降级！")
    elif "degraded" in terminal_status:
        print(f"\n⚠️  仍有降级：{terminal_status} — 请检查 Gate1/Gate2 失败原因")
    else:
        print(f"\n📋 terminal_status: {terminal_status}")

    return run_dir


if __name__ == "__main__":
    main()
