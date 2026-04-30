"""
batch_create_cases.py — 批量生成综合评估用例（v3.1.5）
"""

import os
from pathlib import Path

DATASET_DIR = Path("/Users/benyuhang/Desktop/longtext_project/longtext_v3.1 _fix/evals/datasets/comprehensive")

THEMES = [
    ("Space Exploration", "space", "professional"),
    ("Renaissance Art", "art", "general"),
    ("Quantum Computing", "science", "professional"),
    ("Sustainable Architecture", "architecture", "general"),
    ("History of Silk Road", "history", "general"),
    ("Genetic Engineering", "biology", "professional"),
    ("Future of Remote Work", "work", "general"),
    ("Climate Change Mitigation", "climate", "professional"),
    ("Philosophy of Happiness", "philosophy", "general"),
    ("Evolution of Jazz", "music", "general"),
    ("Autonomous Vehicles Safety", "tech", "professional"),
    ("Ancient Mayan Civilization", "history", "general"),
    ("Cybersecurity Best Practices", "tech", "professional"),
    ("Psychology of Habits", "psychology", "general"),
    ("Deep Sea Mysteries", "nature", "general"),
    ("Blockchain in Supply Chain", "finance", "professional"),
    ("Gourmet Coffee Culture", "lifestyle", "general"),
    ("Urban Farming Solutions", "ecology", "general"),
    ("Rise of E-sports", "sports", "general"),
    ("Impact of Social Media", "society", "general")
]

TEXT_TEMPLATE = """
# 关于 {title} 的深度探讨

这是针对 {title} 领域的深度分析文章。
内容涵盖了背景、核心技术/观点、实际应用场景以及未来的挑战。

## 背景
在当前的时代背景下，{title} 正在经历前所未有的变革。无论是在技术层面还是社会认知层面，都呈现出新的态势。

## 核心要点
1. 创新驱动：不断涌现的新技术是推动该领域发展的核心动力。
2. 数据支撑：通过大量实证数据证明了该趋势的必然性。
3. 跨界融合：与其他领域的交叉融合产生了意想不到的化学反应。

## 挑战与机遇
尽管前景广阔，但我们仍面临着诸多挑战，包括但不限于法律合规、技术壁垒以及伦理争议。

## 结论
未来，{title} 将继续影响我们的生活，并重塑相关产业的格局。
"""

def main():
    # 已经有的编号到 005
    start_idx = 6
    total_target = 50
    
    for i in range(start_idx, total_target + 1):
        case_id = f"comp_{i:03d}"
        theme_idx = (i - start_idx) % len(THEMES)
        title, tag, user_type = THEMES[theme_idx]
        
        full_title = f"{title} (Vol.{i})"
        
        # 写 txt
        txt_path = DATASET_DIR / f"{case_id}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(TEXT_TEMPLATE.format(title=full_title))
            
        # 写 meta
        platform = "xiaohongshu" if i % 2 == 0 else "wechat_official"
        fmt = "image" if i % 3 != 0 else "video"
        
        meta_content = f"""case_id: {case_id}
category: comprehensive
title: {full_title}
source_url: null
text_length: 500
language: zh
preferences:
  target_platform: {platform}
  output_format: {fmt}
  style_hint: auto
user_profile:
  user_type: {user_type}
"""
        meta_path = DATASET_DIR / f"{case_id}.meta.yaml"
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(meta_content)

    print(f"✅ 成功生成 {total_target - start_idx + 1} 个测试用例。")

if __name__ == "__main__":
    main()
