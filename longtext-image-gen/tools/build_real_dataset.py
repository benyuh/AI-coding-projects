"""
tools/build_real_dataset.py — 一次性脚本：将 tests/manual_0429/ 的 10 篇真实长文
转成 comprehensive 数据集（comp_R01–R10）。

执行：
    cd "/Users/benyuhang/Desktop/longtext_project/longtext_v3.1 _fix"
    python3.11 tools/build_real_dataset.py
"""
import sys
import shutil
from pathlib import Path

import yaml

# ── 项目根 ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
SRC_DIR = ROOT / "tests" / "manual_0429"
DST_DIR = ROOT / "evals" / "datasets" / "comprehensive"
ARCHIVE_DIR = DST_DIR / "_archived_fake"

# ── 映射表（严格按 Phase 1 指令）────────────────────────────────────────────
MAPPING = [
    # (源文件前缀, 新 case_id, 标题, platform, output_format, user_type)
    (
        "01_北京义务教育入学政策_0429人工测试",
        "comp_R01",
        "北京义务教育入学政策",
        "xiaohongshu", "image", "general",
    ),
    (
        "02_光伏行业发展报告_0429人工测试",
        "comp_R02",
        "光伏行业发展报告",
        "wechat_official", "image", "professional",
    ),
    (
        "03_全球生育率危机深度报道_0429人工测试",
        "comp_R03",
        "全球生育率危机深度报道",
        "wechat_official", "image", "professional",
    ),
    (
        "04_比特币基础知识科普_0429人工测试",
        "comp_R04",
        "比特币基础知识科普",
        "xiaohongshu", "image", "general",
    ),
    (
        "05_海参养生功效全面评测_0429人工测试",
        "comp_R05",
        "海参养生功效全面评测",
        "xiaohongshu", "image", "general",
    ),
    (
        "06_大语言模型LLM完整科普_0429人工测试",
        "comp_R06",
        "大语言模型LLM完整科普",
        "xiaohongshu", "video", "general",
    ),
    (
        "07_区块链技术深度解析_0429人工测试",
        "comp_R07",
        "区块链技术深度解析",
        "wechat_official", "image", "professional",
    ),
    (
        "08_中国新能源汽车行业深度分析_0429人工测试",
        "comp_R08",
        "中国新能源汽车行业深度分析",
        "wechat_official", "image", "professional",
    ),
    (
        "09_中医养生完全指南_0429人工测试",
        "comp_R09",
        "中医养生完全指南",
        "xiaohongshu", "image", "general",
    ),
    (
        "10_播客逐字稿_中国人口趋势_0429人工测试",
        "comp_R10",
        "播客逐字稿：中国人口趋势",
        "xiaohongshu", "video", "general",
    ),
]


def main():
    # 1. 归档假数据（comp_006–050）
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = 0
    for f in DST_DIR.glob("comp_0[0-9][0-9]*"):
        if f.name.startswith("_archived"):
            continue
        shutil.move(str(f), str(ARCHIVE_DIR / f.name))
        archived += 1
    print(f"[build_real_dataset] 归档占位文件 {archived} 个 → {ARCHIVE_DIR}")

    # 2. 复制真实文章 + 生成 meta.yaml
    for src_prefix, case_id, title, platform, output_format, user_type in MAPPING:
        src_txt = SRC_DIR / f"{src_prefix}.txt"
        if not src_txt.exists():
            print(f"[ERROR] 源文件不存在: {src_txt}")
            sys.exit(1)

        text = src_txt.read_text(encoding="utf-8")
        text_length = len(text)

        # 复制 .txt
        dst_txt = DST_DIR / f"{case_id}.txt"
        shutil.copy(str(src_txt), str(dst_txt))

        # 生成 .meta.yaml
        meta = {
            "case_id": case_id,
            "category": "comprehensive",
            "title": title,
            "source_url": None,
            "text_length": text_length,
            "language": "zh",
            "preferences": {
                "target_platform": platform,
                "output_format": output_format,
                "style_hint": "auto",
            },
            "user_profile": {
                "user_type": user_type,
            },
        }
        dst_meta = DST_DIR / f"{case_id}.meta.yaml"
        with open(dst_meta, "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        print(f"  ✓ {case_id}: {title} [{platform}/{output_format}/{user_type}] ({text_length} chars)")

    # 3. 验证
    print("\n[build_real_dataset] 验证结果：")
    txt_files = sorted(DST_DIR.glob("comp_R*.txt"))
    meta_files = sorted(DST_DIR.glob("comp_R*.meta.yaml"))
    print(f"  .txt  files: {len(txt_files)}")
    print(f"  .meta files: {len(meta_files)}")
    if len(txt_files) != 10 or len(meta_files) != 10:
        print("[ERROR] 文件数量不符合预期，请检查！")
        sys.exit(1)
    for f in txt_files:
        print(f"    {f.name}")
    print("\n✅ comp_R01–R10 数据集构建完成。")


if __name__ == "__main__":
    main()
