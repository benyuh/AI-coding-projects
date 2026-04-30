"""ffmpeg 装好后单跑 comp_R06 + comp_R10，补齐视频端到端验证。"""
import sys, time, json, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from orchestrator.graph import run_pipeline_with_gates
from ir.models import OutputFormat
from infra.tracing import start_trace_capture

CASES = ["comp_R06", "comp_R10"]
RUN_DIR = Path("evals/runs/PHASE_FINAL_video_revalidation")
RUN_DIR.mkdir(parents=True, exist_ok=True)

results = []
for case_id in CASES:
    text = (Path("evals/datasets/comprehensive") / f"{case_id}.txt").read_text("utf-8")
    out_dir = RUN_DIR / case_id / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "output.mp4"

    print(f"\n=== {case_id} 开始 (字数 {len(text)}) ===")
    ctx = start_trace_capture()
    t0 = time.time()
    try:
        final_path, state = run_pipeline_with_gates(
            text=text, output_path=str(out_path), output_format=OutputFormat.VIDEO,
        )
        elapsed = time.time() - t0
        artifacts = list(out_dir.glob("*"))
        has_mp4 = any(a.suffix == ".mp4" for a in artifacts)
        has_srt = any(a.suffix == ".srt" for a in artifacts)
        token_in = sum(c.get("tokens_in", 0) for ev in ctx.events for c in ev.model_calls)
        results.append({
            "case_id": case_id, "elapsed_s": round(elapsed, 1),
            "token_in": token_in, "final_path": final_path,
            "artifacts": [a.name + f" ({a.stat().st_size}B)" for a in artifacts],
            "has_mp4": has_mp4, "has_srt": has_srt,
            "degradation_level": state.get("degradation_level", ""),
        })
        print(f"OK: {case_id} elapsed={elapsed:.1f}s mp4={has_mp4} srt={has_srt}")
    except Exception as e:
        print(f"FAIL: {case_id} {e}")
        results.append({"case_id": case_id, "error": str(e)})
    time.sleep(2)

(RUN_DIR / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
print("\n=== 总结 ===")
print(json.dumps(results, ensure_ascii=False, indent=2))
