"""
tests/eval/run_golden.py — Golden / Snapshot 回归测试（v3.1.5）
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 将项目根目录加入 python path
project_root = str(Path(__file__).parent.parent.parent)
sys.path.append(project_root)

from workers.worker_d_style import run_worker_d
from ir.models import RouterDecision, ArticleType, NarrativeStructure, Platform, UserType

GOLDEN_DIR = Path(project_root) / "evals" / "datasets" / "golden"

def test_worker_d_style():
    """测试 Worker D 风格选择逻辑。"""
    cases = [
        {
            "id": "style_data_journalism",
            "input": RouterDecision(
                narrative_structure=NarrativeStructure.PYRAMID_ARGUMENT,
                article_type=ArticleType.RESEARCH,
                user_type=UserType.PROFESSIONAL,
                platform=Platform.XIAOHONGSHU
            )
        },
        {
            "id": "style_xiaohongshu_warm",
            "input": RouterDecision(
                narrative_structure=NarrativeStructure.THOUGHT_JOURNEY,
                article_type=ArticleType.OPINION,
                user_type=UserType.GENERAL,
                platform=Platform.XIAOHONGSHU
            )
        },
        {
            "id": "style_wechat_moments_tweak",
            "input": RouterDecision(
                narrative_structure=NarrativeStructure.CHRONOLOGICAL_TIMELINE,
                article_type=ArticleType.NEWS,
                user_type=UserType.GENERAL,
                platform=Platform.WECHAT_MOMENTS
            )
        }
    ]
    
    success_count = 0
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    
    update_mode = "--update" in sys.argv
    
    print(f"🔍 运行 Golden 回归测试: WorkerD.Style {'(更新模式)' if update_mode else ''}")
    
    for case in cases:
        case_id = case["id"]
        rd = case["input"]
        actual_tokens = run_worker_d(rd)
        
        golden_file = GOLDEN_DIR / f"{case_id}.json"
        
        # 提取关键字段进行对比
        actual_dict = {
            "style_id": actual_tokens.style_id,
            "font_size_base": actual_tokens.font_size_base,
            "border_radius": actual_tokens.border_radius
        }
        
        if update_mode or not golden_file.exists():
            with open(golden_file, "w", encoding="utf-8") as f:
                json.dump(actual_dict, f, indent=2)
            print(f"✅ {case_id}: 已创建/更新 Golden 文件")
            success_count += 1
            continue
            
        with open(golden_file, "r", encoding="utf-8") as f:
            expected_dict = json.load(f)
            
        if actual_dict == expected_dict:
            print(f"✅ {case_id}: 通过")
            success_count += 1
        else:
            print(f"❌ {case_id}: 失败!")
            print(f"   预期: {expected_dict}")
            print(f"   实际: {actual_dict}")

    return success_count, len(cases)

def main():
    s, t = test_worker_d_style()
    print(f"\n📊 统计: {s}/{t} 通过")
    if s < t:
        sys.exit(1)

if __name__ == "__main__":
    main()
