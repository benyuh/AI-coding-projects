"""
tools/inspect_delivery.py — v3.1.5 交付件巡检脚本

运行：
    cd "/Users/benyuhang/Desktop/longtext_project/longtext_v3.1 _fix"
    python3.11 tools/inspect_delivery.py
"""
import csv
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent

COMP_RUN = ROOT / "evals/runs/20260429_1527_comprehensive"
FUNC_RUN = ROOT / "evals/runs/20260429_1610_functional"
EDGE_RUN = ROOT / "evals/runs/20260429_1626_edge"
REPORT_DIR = ROOT / "evals/reports/comprehensive_20260429_1527_comprehensive"


def load_manifest(run_dir: Path) -> dict:
    p = run_dir / "manifest.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_summary(run_dir: Path) -> list[dict]:
    p = run_dir / "summary.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def check_artifacts(run_dir: Path, cases: list[str]) -> list[str]:
    missing = []
    for case_id in cases:
        art = run_dir / case_id / "artifacts" / "output.png"
        if not art.exists():
            missing.append(f"{case_id}: output.png 缺失")
        elif art.stat().st_size < 50_000:
            missing.append(f"{case_id}: output.png 过小 ({art.stat().st_size} bytes)")
    return missing


def red_flags(rows: list[dict]) -> list[str]:
    issues = []
    for row in rows:
        rc = row.get("realness_check", "ok")
        if rc and rc != "ok":
            issues.append(f"  ⚠️  {row['case_id']}: {rc}")
    return issues


def print_run_summary(label: str, run_dir: Path):
    m = load_manifest(run_dir)
    rows = load_summary(run_dir)
    if not m:
        print(f"  {label}: manifest 不存在 ({run_dir})")
        return

    tok_in = m.get("total_tokens", {}).get("sonnet_in", 0)
    tok_out = m.get("total_tokens", {}).get("sonnet_out", 0)
    total_sec = m.get("total_duration_sec", 0)
    n = m.get("cases_total", 0)
    avg_sec = round(total_sec / n, 1) if n else 0

    print(f"  {label}")
    print(f"    run_id     : {m.get('run_id', 'N/A')}")
    print(f"    cases      : total={n} succeeded={m.get('cases_succeeded')} degraded={m.get('cases_degraded')} rejected={m.get('cases_rejected')}")
    print(f"    tokens     : sonnet_in={tok_in:,}  sonnet_out={tok_out:,}")
    print(f"    duration   : total={total_sec}s  avg={avg_sec}s/case")

    flags = red_flags(rows)
    if flags:
        print(f"    🔴 realness issues ({len(flags)}):")
        for f in flags:
            print(f"   {f}")
    else:
        print(f"    ✅ realness_check: all ok")


# ── 1. 核心交付件路径 ────────────────────────────────────────────────────────

print("=" * 60)
print("longtext v3.1.5 交付件巡检")
print("=" * 60)

print("\n[1] 核心交付件路径")
files = [
    ("DELIVERY_v3.1.5.md",          ROOT / "DELIVERY_v3.1.5.md"),
    ("综合评估报告 final_report.md", REPORT_DIR / "final_report.md"),
    ("综合评估 summary.csv",         COMP_RUN / "summary.csv"),
    ("功能测试 summary.csv",         FUNC_RUN / "summary.csv"),
    ("Edge 抽样 summary.csv",        EDGE_RUN / "summary.csv"),
    ("orchestrator/graph.py",        ROOT / "orchestrator/graph.py"),
    ("tests/eval/runner.py",         ROOT / "tests/eval/runner.py"),
]
for name, path in files:
    status = "✅" if path.exists() else "❌ 缺失"
    size = f"({path.stat().st_size // 1024}KB)" if path.exists() else ""
    print(f"  {status}  {name}  {size}")

# ── 2. 各 run 真实性摘要 ───────────────────────────────────────────────────

print("\n[2] 各 run 真实性摘要")
print_run_summary("综合评估 (20260429_1527_comprehensive)", COMP_RUN)
print()
print_run_summary("功能测试 (20260429_1610_functional)", FUNC_RUN)
print()
print_run_summary("Edge 抽样 (20260429_1626_edge)", EDGE_RUN)

# ── 3. 产物完整性检查 ─────────────────────────────────────────────────────

print("\n[3] 产物完整性检查")
comp_cases = [f"comp_R{i:02d}" for i in range(1, 11)]
comp_missing = check_artifacts(COMP_RUN, comp_cases)
if comp_missing:
    print(f"  ❌ 综合评估产物缺失: {comp_missing}")
else:
    print(f"  ✅ 综合评估 10/10 产物存在且 > 50KB")

func_cases = [
    "func_01_policy_general", "func_02_policy_pro", "func_03_research_pro",
    "func_04_news_event_general", "func_05_personal_opinion_general",
    "func_06_video_timeline", "func_07_multi_source", "func_08_video_research",
]
func_missing = check_artifacts(FUNC_RUN, func_cases)
if func_missing:
    print(f"  ⚠️  功能测试产物（视频降级为 PNG 属预期）: {func_missing}")
else:
    print(f"  ✅ 功能测试 8/8 产物存在且 > 50KB")

# ── 4. realness_check 红旗汇总 ────────────────────────────────────────────

print("\n[4] realness_check 红旗汇总（非 ok 的 case）")
all_flags: list[str] = []
for label, run_dir in [("综合", COMP_RUN), ("功能", FUNC_RUN), ("Edge", EDGE_RUN)]:
    rows = load_summary(run_dir)
    flags = red_flags(rows)
    for f in flags:
        all_flags.append(f"[{label}] {f.strip()}")

if all_flags:
    for f in all_flags:
        print(f"  {f}")
    print(f"\n  说明：'format=video 但产物无 .mp4' 为已知预期降级（ffmpeg 未装）")
    print(f"        '耗时仅 Xs (疑似 fallback)' 对 BLOCKED case 属正常（Agent1 直接拦截，无需全链路运行）")
else:
    print("  ✅ 无红旗")

print("\n" + "=" * 60)
print("巡检完成")
print("=" * 60)
