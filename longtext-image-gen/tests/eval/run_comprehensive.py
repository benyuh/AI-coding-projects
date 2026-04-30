"""
tests/eval/run_comprehensive.py — 运行综合评估（Comprehensive）
"""

import os
import sys
import csv
from pathlib import Path

# 将项目根目录加入 python path
project_root = str(Path(__file__).parent.parent.parent)
sys.path.append(project_root)

from tests.eval.runner import EvalRunner

def main():
    runner = EvalRunner(project_root)
    run_id = runner.run_category("comprehensive")
    print(f"\n✨ 综合评估完成! Run ID: {run_id}")
    
    # 生成打分表
    report_dir = runner.reports_dir / f"comprehensive_{run_id}"
    report_dir.mkdir(parents=True, exist_ok=True)
    
    scoring_sheet_path = report_dir / "scoring_sheet.csv"
    
    # 读取 run 目录下的所有 case
    run_dir = runner.runs_dir / run_id
    cases = [d.name for d in run_dir.iterdir() if d.is_dir()]
    cases.sort()
    
    headers = [
        "case_id", "terminal_status", "format",
        "图-信息保真度(1-5)", "图-信息紧凑度(1-5)", "图-视觉风格(1-5)", "图-受众适配(1-5)", "图-可发布度(1-5)",
        "视-配音流畅度(1-5)", "视-音画同步(1-5)", "视-节奏与转场(1-5)", "视-BGM适配(1-5)",
        "备注"
    ]
    
    with open(scoring_sheet_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for case_id in cases:
            # 这里可以从 summary.csv 或 result.json 中读一些基础信息
            writer.writerow([case_id, "", "", "", "", "", "", "", "", "", "", "", ""])

    print(f"📊 已生成人工打分卡: {scoring_sheet_path}")
    print(f"查看报告汇总: evals/runs/{run_id}/summary.csv")

if __name__ == "__main__":
    main()
