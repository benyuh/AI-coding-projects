"""tools/diagnostic_hallucination.py — Sprint 0 基线诊断：Hallucination 现状

输入：evals/runs/**/<case_id>/stages/gate1.json
输出：evals/reports/baseline_v3.1.5/baseline_hallucination.md

按每个 case 的 faithfulness 维度分数 + reason 关键词分类，给出严重 (<80) /
中度 (80-89) / 通过 (≥90) 分布，并对 reason 做错误类型抽取。

用法：
    python tools/diagnostic_hallucination.py
    python tools/diagnostic_hallucination.py --runs-dir evals/runs --output evals/reports/baseline_v3.1.5
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ── 错误类型关键词（中文）─────────────────────────────────────────────────────
_ERROR_TAXONOMY: dict[str, list[str]] = {
    "事实改写": ["改写", "改成", "篡改", "误述", "曲解", "误解"],
    "数字截断": ["数字截断", "位数", "截断", "省略", "少了一位", "多了一位"],
    "年份错位": ["年份错位", "年份错误", "时间错位", "时间错误", "日期错", "时间不符"],
    "实体替换": ["实体替换", "实体错误", "人名错", "机构错", "公司错", "替换为", "误为"],
    "编造": ["编造", "捏造", "虚构", "无中生有", "原文未", "原文没有", "原文不存在", "查无", "凭空"],
    "夸大缩小": ["夸大", "缩小", "高估", "低估", "扩大", "渲染"],
}

_SEVERE_THRESHOLD = 80.0    # < 80 视为严重
_PASS_THRESHOLD = 90.0      # >= 90 视为通过


def _classify_error_types(reason: str) -> list[str]:
    """从 reason 文本里抽取错误类型标签（多标签）。"""
    if not reason:
        return []
    hits: list[str] = []
    for label, keywords in _ERROR_TAXONOMY.items():
        if any(kw in reason for kw in keywords):
            hits.append(label)
    return hits


def _bucket(score: float) -> str:
    if score < _SEVERE_THRESHOLD:
        return "severe"
    if score < _PASS_THRESHOLD:
        return "medium"
    return "pass"


def _collect_latest_runs(runs_dir: Path, case_regex: str) -> dict[str, Path]:
    """按 case_id 取最新一次 run 的 stages/ 目录。

    默认只纳入 v3.1.5 红队/交付基线的 18 条：comp_Rxx + func_xx。
    如需全量历史 case，可传 --case-regex '.*'。
    """
    latest: dict[str, Path] = {}
    pattern = re.compile(case_regex)
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("_") or run_dir.name.startswith("."):
            continue
        for case_dir in run_dir.iterdir():
            if not case_dir.is_dir() or not pattern.search(case_dir.name):
                continue
            stages = case_dir / "stages"
            if not (stages / "gate1.json").exists():
                continue
            latest[case_dir.name] = stages
    return latest


def _load_gate1(stages_dir: Path) -> dict[str, Any] | None:
    f = stages_dir / "gate1.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[diagnostic_hallucination] 读取失败 {f}: {e}")
        return None


def _extract_faithfulness(gate1: dict[str, Any]) -> tuple[float, str] | None:
    for d in gate1.get("dim_scores", []):
        if d.get("dimension") == "faithfulness":
            return float(d.get("score", 0.0)), str(d.get("reason", ""))
    return None


def analyze(runs_dir: Path, output_dir: Path, case_regex: str) -> dict[str, Any]:
    cases = _collect_latest_runs(runs_dir, case_regex)
    rows: list[dict[str, Any]] = []
    bucket_counter: Counter[str] = Counter()
    error_counter: Counter[str] = Counter()
    severe_cases_by_error: dict[str, list[str]] = defaultdict(list)

    for case_id, stages in sorted(cases.items()):
        gate1 = _load_gate1(stages)
        if not gate1:
            continue
        f = _extract_faithfulness(gate1)
        if f is None:
            continue
        score, reason = f
        b = _bucket(score)
        types = _classify_error_types(reason)
        bucket_counter[b] += 1
        for t in types:
            error_counter[t] += 1
            if b == "severe":
                severe_cases_by_error[t].append(case_id)
        rows.append({
            "case_id": case_id,
            "score": score,
            "bucket": b,
            "error_types": types,
            "reason": reason,
            "run": stages.parent.parent.name,
        })

    total = len(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "baseline_hallucination.md"
    json_path = output_dir / "baseline_hallucination.json"

    json_path.write_text(json.dumps({
        "total_cases": total,
        "bucket_distribution": dict(bucket_counter),
        "error_type_counter": dict(error_counter),
        "severe_cases_by_error": {k: sorted(v) for k, v in severe_cases_by_error.items()},
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    severe = bucket_counter["severe"]
    medium = bucket_counter["medium"]
    pas = bucket_counter["pass"]

    avg_score = (sum(r["score"] for r in rows) / total) if total else 0.0

    md = []
    md.append("# v3.1.5 Baseline — Hallucination 现状")
    md.append("")
    md.append(f"- 数据源：`{runs_dir}` latest-per-case-id，case_regex=`{case_regex}`")
    md.append(f"- 案例总数：**{total}**")
    md.append(f"- Faithfulness 平均分：**{avg_score:.1f}**")
    md.append("")
    md.append("## 1. 严重程度分布")
    md.append("")
    md.append("| 等级 | 阈值 | 数量 | 占比 |")
    md.append("| --- | --- | --- | --- |")
    md.append(f"| 严重（severe） | <80 | {severe} | {severe/total*100:.1f}% |" if total else "| 严重 | <80 | 0 | - |")
    md.append(f"| 中度（medium） | 80-89 | {medium} | {medium/total*100:.1f}% |" if total else "| 中度 | 80-89 | 0 | - |")
    md.append(f"| 通过（pass） | ≥90 | {pas} | {pas/total*100:.1f}% |" if total else "| 通过 | ≥90 | 0 | - |")
    md.append("")
    md.append("## 2. 错误类型分布（reason 关键词抽取，多标签）")
    md.append("")
    if error_counter:
        md.append("| 错误类型 | 命中次数 | 严重 case 列表 |")
        md.append("| --- | --- | --- |")
        for label, cnt in error_counter.most_common():
            severe_ids = severe_cases_by_error.get(label, [])
            md.append(f"| {label} | {cnt} | {', '.join(severe_ids) if severe_ids else '-'} |")
    else:
        md.append("- 未抽取到匹配关键词的错误类型（reason 文本可能未明确分类）")
    md.append("")
    md.append("## 3. 全部案例明细（按 score 升序）")
    md.append("")
    md.append("| case_id | run | score | bucket | error_types | reason 摘要 |")
    md.append("| --- | --- | ---: | --- | --- | --- |")
    for r in sorted(rows, key=lambda r: r["score"]):
        types_str = ", ".join(r["error_types"]) if r["error_types"] else "-"
        reason = r["reason"][:80].replace("|", "/").replace("\n", " ")
        md.append(f"| {r['case_id']} | {r['run']} | {r['score']:.1f} | {r['bucket']} | {types_str} | {reason} |")
    md.append("")
    md.append("## 4. 验收基线（Sprint 1 验收对照用）")
    md.append("")
    severe_pct = severe / total * 100 if total else 0
    md.append(f"- v3.1.5 baseline 严重占比：**{severe_pct:.1f}%**（v3.1.6 验收门禁 ≤10%）")
    md.append(f"- v3.1.5 baseline 平均分：**{avg_score:.1f}**（v3.1.6 验收门禁 ≥85）")

    md_path.write_text("\n".join(md), encoding="utf-8")

    print(f"[diagnostic_hallucination] 写出 {md_path}")
    print(f"[diagnostic_hallucination] 写出 {json_path}")
    print(f"[diagnostic_hallucination] 严重 {severe}/{total}（{severe_pct:.1f}%），平均 {avg_score:.1f}")

    return {
        "total": total,
        "severe": severe,
        "medium": medium,
        "pass": pas,
        "avg_score": avg_score,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 0 baseline: hallucination diagnostic")
    parser.add_argument("--runs-dir", default="evals/runs", help="evaluation runs root directory")
    parser.add_argument("--output", default="evals/reports/baseline_v3.1.5", help="output report directory")
    parser.add_argument("--case-regex", default=r"^(comp_R\d+|func_)", help="case_id regex filter; use '.*' for all cases")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent.parent
    runs_dir = (here / args.runs_dir).resolve()
    output_dir = (here / args.output).resolve()

    if not runs_dir.exists():
        raise SystemExit(f"runs 目录不存在: {runs_dir}")

    analyze(runs_dir, output_dir, args.case_regex)


if __name__ == "__main__":
    main()
