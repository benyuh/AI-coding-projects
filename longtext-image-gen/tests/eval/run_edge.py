"""
tests/eval/run_edge.py — 运行极限测试（Edge/Stress）
"""

import os
import sys
import yaml
import json
from pathlib import Path

# 将项目根目录加入 python path
project_root = str(Path(__file__).parent.parent.parent)
sys.path.append(project_root)

from tests.eval.runner import EvalRunner
from tests.eval.assertions import assert_edge_behavior

def main():
    runner = EvalRunner(project_root)
    run_id = runner.run_category("edge")
    print(f"\n✨ 极限测试完成! Run ID: {run_id}")
    
    print(f"🔍 正在验证断言...")
    run_dir = runner.runs_dir / run_id
    
    # 加载 manifest
    with open(run_dir / "manifest.yaml", "r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    
    results = []
    for case_id_dir in run_dir.iterdir():
        if not case_id_dir.is_dir():
            continue
        
        case_id = case_id_dir.name
        # 读取 meta
        meta_file = runner.datasets_dir / "edge" / f"{case_id}.meta.yaml"
        if not meta_file.exists():
            continue
            
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        
        expected_behavior = meta.get("expected_behavior", {})
        
        # 读取运行结果 state (从 stages 下组合或者直接从 result.json 和 trace.jsonl 恢复)
        # 这里为了断言，我们需要 state
        # 我们模拟一个 state 对象
        state = {}
        # 从 stages 目录恢复关键字段
        stages_dir = case_id_dir / "stages"
        if (stages_dir / "agent1_router.json").exists():
            with open(stages_dir / "agent1_router.json", "r", encoding="utf-8") as f:
                state["router_decision"] = json.load(f)
        
        if (case_id_dir / "result.json").exists():
            with open(case_id_dir / "result.json", "r", encoding="utf-8") as f:
                res = json.load(f)
                state["degradation_level"] = res.get("degradation_level")
                state["error"] = res.get("error")
        
        # 执行断言
        try:
            assert_edge_behavior(case_id, state, expected_behavior)
            print(f"✅ {case_id}: 行为符合预期")
            results.append((case_id, "PASS"))
        except AssertionError as e:
            print(f"❌ {case_id}: 断言失败 - {e}")
            results.append((case_id, "FAIL", str(e)))
        except Exception as e:
            print(f"⚠️ {case_id}: 验证过程中出错 - {e}")
            results.append((case_id, "ERROR", str(e)))

    # 打印最终统计
    passed = len([r for r in results if r[1] == "PASS"])
    print(f"\n📊 统计: {passed}/{len(results)} 个 case 通过断言")
    print(f"查看报告: evals/runs/{run_id}/summary.csv")

if __name__ == "__main__":
    main()
