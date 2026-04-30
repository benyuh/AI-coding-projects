"""tools/diagnostic_routing.py — Sprint 0 基线诊断：平台路由一致率

输入：
  - evals/runs/**/<case_id>/stages/agent1_router.json
  - evals/datasets/**/<case_id>.meta.yaml (preferences.target_platform)
输出：evals/reports/baseline_v3.1.5/baseline_routing.md

用法：
    python tools/diagnostic_routing.py
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _collect_latest_runs(runs_dir: Path, case_regex: str) -> dict[str, Path]:
    """按 case_id 取最新一次 run；默认纳入 comp_Rxx + func_xx 18 条基线。"""
    latest: dict[str, Path] = {}
    pattern = re.compile(case_regex)
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith(("_", ".")):
            continue
        for case_dir in run_dir.iterdir():
            if not case_dir.is_dir() or not pattern.search(case_dir.name):
                continue
            stages = case_dir / "stages"
            if (stages / "agent1_router.json").exists():
                latest[case_dir.name] = stages
    return latest


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_meta(datasets_dir: Path, case_id: str) -> dict[str, Any]:
    if yaml is None:
        return {}
    for candidate in datasets_dir.glob(f"**/{case_id}.meta.yaml"):
        try:
            return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def _target_platform(meta: dict[str, Any]) -> str:
    preferences = meta.get("preferences") or {}
    target = preferences.get("target_platform") or ""
    return str(target).strip()


def _compatible_target(target: str) -> str:
    """将产品侧偏好归一到当前 Platform enum 粒度。"""
    mapping = {
        "wechat_official": "wechat_moments",
        "wechat": "wechat_moments",
        "moments": "wechat_moments",
        "xhs": "xiaohongshu",
        "rednote": "xiaohongshu",
    }
    return mapping.get(target, target)


def analyze(runs_dir: Path, datasets_dir: Path, output_dir: Path, case_regex: str) -> dict[str, Any]:
    cases = _collect_latest_runs(runs_dir, case_regex)
    rows: list[dict[str, Any]] = []
    exact_counter: Counter[str] = Counter()
    compatible_counter: Counter[str] = Counter()
    target_counter: Counter[str] = Counter()
    actual_counter: Counter[str] = Counter()
    bias_counter: Counter[str] = Counter()
    mismatch_by_category: dict[str, list[str]] = defaultdict(list)

    for case_id, stages in sorted(cases.items()):
        router = _load_json(stages / "agent1_router.json")
        meta = _load_meta(datasets_dir, case_id)
        target = _target_platform(meta)
        if not target or target == "auto":
            continue

        actual = str(router.get("platform", ""))
        normalized_target = _compatible_target(target)
        exact_match = actual == target
        compatible_match = actual == normalized_target
        category = str(meta.get("category") or meta.get("sub_category") or "unknown")

        exact_counter["match" if exact_match else "mismatch"] += 1
        compatible_counter["match" if compatible_match else "mismatch"] += 1
        target_counter[target] += 1
        actual_counter[actual] += 1
        if not compatible_match:
            bias_counter[f"{target}->{actual}"] += 1
            mismatch_by_category[category].append(case_id)

        rows.append({
            "case_id": case_id,
            "run": stages.parent.parent.name,
            "category": category,
            "target_platform": target,
            "normalized_target": normalized_target,
            "actual_platform": actual,
            "exact_match": exact_match,
            "compatible_match": compatible_match,
            "article_type": router.get("article_type", ""),
            "style_hint": router.get("style_hint", ""),
        })

    total = len(rows)
    exact_matches = exact_counter["match"]
    compatible_matches = compatible_counter["match"]
    exact_rate = exact_matches / total if total else 0.0
    compatible_rate = compatible_matches / total if total else 0.0

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "baseline_routing.md"
    json_path = output_dir / "baseline_routing.json"
    json_path.write_text(json.dumps({
        "total_cases_with_target_platform": total,
        "exact_match_rate": exact_rate,
        "compatible_match_rate": compatible_rate,
        "target_counter": dict(target_counter),
        "actual_counter": dict(actual_counter),
        "bias_counter": dict(bias_counter),
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    md: list[str] = []
    md.append("# v3.1.5 Baseline — 平台路由一致率")
    md.append("")
    md.append(f"- 数据源：`{runs_dir}` latest-per-case-id，case_regex=`{case_regex}`")
    md.append(f"- 有 target_platform 的案例数：**{total}**")
    md.append(f"- 严格一致率：**{exact_matches}/{total} ({exact_rate*100:.1f}%)**")
    md.append(f"- 兼容一致率（wechat_official 归一到 wechat_moments）：**{compatible_matches}/{total} ({compatible_rate*100:.1f}%)**")
    md.append("")
    md.append("## 1. 平台分布")
    md.append("")
    md.append("| type | platform | count |")
    md.append("| --- | --- | ---: |")
    for platform, count in target_counter.most_common():
        md.append(f"| target | {platform} | {count} |")
    for platform, count in actual_counter.most_common():
        md.append(f"| actual | {platform} | {count} |")
    md.append("")
    md.append("## 2. 偏置方向（按兼容口径统计）")
    md.append("")
    if bias_counter:
        md.append("| target -> actual | count |")
        md.append("| --- | ---: |")
        for direction, count in bias_counter.most_common():
            md.append(f"| {direction} | {count} |")
    else:
        md.append("- 无兼容口径 mismatch")
    md.append("")
    md.append("## 3. mismatch 分类")
    md.append("")
    if mismatch_by_category:
        for category, case_ids in sorted(mismatch_by_category.items()):
            md.append(f"- **{category}**: {len(case_ids)} ({', '.join(sorted(case_ids))})")
    else:
        md.append("- 无")
    md.append("")
    md.append("## 4. 全部案例明细")
    md.append("")
    md.append("| case_id | run | category | target | normalized | actual | exact | compatible | article_type | style |")
    md.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        md.append(
            f"| {r['case_id']} | {r['run']} | {r['category']} | {r['target_platform']} | "
            f"{r['normalized_target']} | {r['actual_platform']} | {r['exact_match']} | "
            f"{r['compatible_match']} | {r['article_type']} | {r['style_hint']} |"
        )
    md.append("")
    md.append("## 5. v3.1.6 验收对照基线")
    md.append("")
    md.append(f"- baseline strict match = **{exact_rate*100:.1f}%** → 验收 100%")
    md.append(f"- baseline compatible match = **{compatible_rate*100:.1f}%** → 验收 100%")
    md.append("- 若用户指定 target_platform，Agent1/Orchestrator 必须用代码强制覆盖 LLM 输出")

    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[diagnostic_routing] 写出 {md_path}")
    print(f"[diagnostic_routing] 写出 {json_path}")
    print(f"[diagnostic_routing] strict={exact_rate*100:.1f}%, compatible={compatible_rate*100:.1f}%")

    return {"total": total, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 0 baseline: routing diagnostic")
    parser.add_argument("--runs-dir", default="evals/runs")
    parser.add_argument("--datasets-dir", default="evals/datasets")
    parser.add_argument("--output", default="evals/reports/baseline_v3.1.5")
    parser.add_argument("--case-regex", default=r"^(comp_R\d+|func_)", help="case_id regex filter; use '.*' for all cases")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent.parent
    runs_dir = (here / args.runs_dir).resolve()
    datasets_dir = (here / args.datasets_dir).resolve()
    output_dir = (here / args.output).resolve()

    if not runs_dir.exists():
        raise SystemExit(f"runs 目录不存在: {runs_dir}")
    if yaml is None:
        print("[diagnostic_routing] 警告：缺少 PyYAML，meta.yaml 解析将跳过")

    analyze(runs_dir, datasets_dir, output_dir, args.case_regex)


if __name__ == "__main__":
    main()
