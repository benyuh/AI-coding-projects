"""
tests/eval/run_functional.py — 运行功能测试（Smoke）
"""

import os
import sys
from pathlib import Path

# 将项目根目录加入 python path
project_root = str(Path(__file__).parent.parent.parent)
sys.path.append(project_root)

from tests.eval.runner import EvalRunner

def main():
    runner = EvalRunner(project_root)
    # 运行全量功能测试
    run_id = runner.run_category("functional")
    print(f"\n✨ 功能测试完成! Run ID: {run_id}")
    print(f"查看报告: evals/runs/{run_id}/summary.csv")

if __name__ == "__main__":
    main()
