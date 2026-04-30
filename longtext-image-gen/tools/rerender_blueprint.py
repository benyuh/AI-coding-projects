"""一次性重渲染工具（不调 LLM，零 token 成本）。

读取已有 run 目录下的 blueprint.json，重新走 Tool1 渲染流程，输出新的 PNG。
用于：修了渲染层 bug 后，只需要刷新视觉产物，不必重跑整个 Pipeline。

用法：
    python3 tools/rerender_blueprint.py evals/runs/demo_safe_20260430_001851_comp_R04
    python3 tools/rerender_blueprint.py evals/runs/demo_safe_xxx \\
                                        evals/runs/demo_safe_yyy ...
    python3 tools/rerender_blueprint.py --all  # 重渲所有 demo_safe_* 目录
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ir.models import Blueprint
from tools.tool1_image_render import run_tool1_render


def rerender_one(run_dir: Path) -> dict:
    """重渲染一个 run 目录。返回 {ok, run_dir, png_size, error}"""
    bp_path = run_dir / "blueprint.json"
    if not bp_path.exists():
        return {"ok": False, "run_dir": str(run_dir), "error": "blueprint.json missing"}

    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_png = artifacts_dir / "output.png"

    # 备份旧 PNG（用于事后对比）
    if out_png.exists():
        backup = artifacts_dir / "output.before_none_fix.png"
        if not backup.exists():
            out_png.rename(backup)
        else:
            out_png.unlink()

    try:
        bp_data = json.loads(bp_path.read_text(encoding="utf-8"))
        blueprint = Blueprint.model_validate(bp_data)
        artifact = run_tool1_render(blueprint, str(out_png))
        size = out_png.stat().st_size if out_png.exists() else 0
        return {
            "ok": True,
            "run_dir": str(run_dir),
            "png_size": size,
            "png_path": str(out_png),
            "render_ms": getattr(artifact, "elapsed_ms", None),
        }
    except Exception as e:
        import traceback
        return {
            "ok": False,
            "run_dir": str(run_dir),
            "error": str(e),
            "trace": traceback.format_exc(),
        }


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args == ["--all"]:
        targets = sorted((ROOT / "evals/runs").glob("demo_safe_*"))
    else:
        targets = [Path(a) if Path(a).is_absolute() else ROOT / a for a in args]

    print(f"=== 重渲染 {len(targets)} 个 run ===")
    results = []
    for t in targets:
        print(f"\n[{t.name}] 开始重渲染...")
        r = rerender_one(t)
        results.append(r)
        if r["ok"]:
            print(f"  ✓ PNG: {r['png_size']:,} bytes -> {r['png_path']}")
        else:
            print(f"  ✗ FAIL: {r.get('error')}")
            if "trace" in r:
                print(r["trace"][:500])

    print("\n=== 汇总 ===")
    ok = sum(1 for r in results if r["ok"])
    print(f"成功 {ok}/{len(results)}")
    for r in results:
        name = Path(r["run_dir"]).name
        if r["ok"]:
            print(f"  ✓ {name}: {r['png_size']:,} bytes")
        else:
            print(f"  ✗ {name}: {r.get('error')}")


if __name__ == "__main__":
    main()
