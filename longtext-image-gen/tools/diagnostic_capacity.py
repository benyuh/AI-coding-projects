"""tools/diagnostic_capacity.py — Sprint 0 基线诊断：容量与覆盖率

输入：
  - evals/runs/**/<case_id>/stages/gate1.json (completeness 维度)
  - evals/runs/**/<case_id>/stages/agent2_understanding.json (claims 数 / card_budget / outline)
  - evals/runs/**/<case_id>/stages/tool_render.json (实际 card_count)
  - evals/datasets/**/<case_id>.meta.yaml (text_length)
  - evals/datasets/**/<case_id>.txt (原文，用于章节统计)
输出：evals/reports/baseline_v3.1.5/baseline_capacity.md

核心指标：
- 原文章节数（按 # / ## / ### 分割）vs Blueprint 卡片数 vs completeness 分
- 物理覆盖率：Blueprint 中实际使用的 chunk_id 占总 chunk 数的比例（v3.1.5 没存
  source_offset，所以这里只能估算：用 claims 数 / 章节数作为近似覆盖率）

用法：
    python tools/diagnostic_capacity.py
"""

from __future__ import annotations

import argparse
import json
import re
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
        if not run_dir.is_dir() or run_dir.name.startswith("_") or run_dir.name.startswith("."):
            continue
        for case_dir in run_dir.iterdir():
            if not case_dir.is_dir() or not pattern.search(case_dir.name):
                continue
            if (case_dir / "stages" / "gate1.json").exists():
                latest[case_dir.name] = case_dir / "stages"
    return latest


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_meta(datasets_dir: Path, case_id: str) -> dict[str, Any] | None:
    if yaml is None:
        return None
    for category in ("comprehensive", "functional", "edge"):
        candidate = datasets_dir / category / f"{case_id}.meta.yaml"
        if candidate.exists():
            try:
                return yaml.safe_load(candidate.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _load_source_text(datasets_dir: Path, case_id: str) -> str | None:
    for category in ("comprehensive", "functional", "edge"):
        category_dir = datasets_dir / category
        candidate = category_dir / f"{case_id}.txt"
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8")
            except Exception:
                pass
        multi_source_parts = sorted(category_dir.glob(f"{case_id}_s*.txt"))
        if multi_source_parts:
            texts = []
            for part in multi_source_parts:
                try:
                    texts.append(part.read_text(encoding="utf-8"))
                except Exception:
                    pass
            if texts:
                return "\n\n".join(texts)
    return None


_HEADING_RE = re.compile(
    r"^(?:#{1,4}\s+\S|[一二三四五六七八九十]+[、.．]|\d+[、.．]|[（(][一二三四五六七八九十]+[）)])",
    re.MULTILINE,
)
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def _count_sections(text: str) -> int:
    """按 markdown/中文编号标题估算原文章节数。"""
    if not text:
        return 0
    headings = len(_HEADING_RE.findall(text))
    if headings >= 2:
        return headings
    # 没有明显标题时退化为 chunk 数近似，避免把普通段落全算成章节。
    paras = [p for p in _PARA_SPLIT_RE.split(text) if len(p.strip()) >= 80]
    char_chunks = max(1, len(text) // 800)
    return max(min(len(paras), char_chunks), char_chunks)


def _extract_completeness(gate1: dict[str, Any]) -> tuple[float, str] | None:
    for d in gate1.get("dim_scores", []):
        if d.get("dimension") == "completeness":
            return float(d.get("score", 0.0)), str(d.get("reason", ""))
    return None


def analyze(
    runs_dir: Path,
    datasets_dir: Path,
    output_dir: Path,
    case_regex: str,
) -> dict[str, Any]:
    cases = _collect_latest_runs(runs_dir, case_regex)
    rows: list[dict[str, Any]] = []

    for case_id, stages in sorted(cases.items()):
        gate1 = _load_json(stages / "gate1.json") or {}
        agent2 = _load_json(stages / "agent2_understanding.json") or {}
        render = _load_json(stages / "tool_render.json") or {}
        meta = _load_meta(datasets_dir, case_id) or {}
        text = _load_source_text(datasets_dir, case_id) or ""

        comp = _extract_completeness(gate1)
        if comp is None:
            continue
        comp_score, comp_reason = comp

        sections = _count_sections(text)
        text_len = meta.get("text_length") or len(text)
        if isinstance(text_len, str):
            text_len_int = int(re.sub(r"[^\d]", "", text_len) or 0)
        else:
            text_len_int = int(text_len)

        claims = len(agent2.get("claims", []))
        card_budget = int(agent2.get("card_budget", 0))
        rendered_cards = int(render.get("card_count", 0))

        # 物理覆盖率近似：claims 数 / 章节数（>1 时截到 1）
        coverage = min(1.0, claims / sections) if sections else 0.0

        rows.append({
            "case_id": case_id,
            "run": stages.parent.parent.name,
            "text_length": text_len_int,
            "sections": sections,
            "claims": claims,
            "card_budget": card_budget,
            "rendered_cards": rendered_cards,
            "completeness": comp_score,
            "coverage_estimate": coverage,
            "completeness_reason": comp_reason,
        })

    total = len(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "baseline_capacity.md"
    json_path = output_dir / "baseline_capacity.json"

    json_path.write_text(json.dumps({
        "total_cases": total,
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    avg_completeness = (sum(r["completeness"] for r in rows) / total) if total else 0.0
    avg_coverage = (sum(r["coverage_estimate"] for r in rows) / total) if total else 0.0
    low_coverage_cases = [r for r in rows if r["coverage_estimate"] < 0.6]
    over_long = [r for r in rows if r["text_length"] >= 8000]

    md: list[str] = []
    md.append("# v3.1.5 Baseline — 容量与物理覆盖率")
    md.append("")
    md.append(f"- 数据源：`{runs_dir}` latest-per-case-id，case_regex=`{case_regex}`")
    md.append(f"- 案例总数：**{total}**")
    md.append(f"- Completeness 平均分：**{avg_completeness:.1f}**（v3.1.6 门禁 ≥80）")
    md.append(f"- 物理覆盖率近似平均：**{avg_coverage*100:.1f}%**（v3.1.6 门禁 ≥80%）")
    md.append(f"- 长文本（≥8000 字）案例：**{len(over_long)}** 个" + ("，预计 Agent 2 中段截断风险高" if over_long else "，当前基线主要暴露中等长文覆盖不足"))
    md.append("")
    md.append("## 1. 各案例容量明细（按 completeness 升序）")
    md.append("")
    md.append("| case_id | run | text_len | 章节数 | claims | budget | rendered | comp | coverage | reason 摘要 |")
    md.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in sorted(rows, key=lambda r: r["completeness"]):
        reason = r["completeness_reason"][:60].replace("|", "/").replace("\n", " ")
        md.append(
            f"| {r['case_id']} | {r['run']} | {r['text_length']} | {r['sections']} | "
            f"{r['claims']} | {r['card_budget']} | {r['rendered_cards']} | "
            f"{r['completeness']:.1f} | {r['coverage_estimate']*100:.0f}% | {reason} |"
        )
    md.append("")
    md.append("## 2. 低覆盖率案例（coverage < 60%）")
    md.append("")
    if low_coverage_cases:
        for r in sorted(low_coverage_cases, key=lambda r: r["coverage_estimate"]):
            md.append(f"- **{r['case_id']}**: coverage={r['coverage_estimate']*100:.0f}%, "
                      f"sections={r['sections']}, claims={r['claims']}, "
                      f"text={r['text_length']} 字")
    else:
        md.append("- 无")
    md.append("")
    md.append("## 3. 长文本截断风险（text_length ≥ 8000）")
    md.append("")
    if over_long:
        md.append("Agent 2 当前在 >8000 字时只截首 6000 + 尾 2000，中段必然丢失。这些案例最优先验证。")
        md.append("")
        for r in over_long:
            md.append(f"- **{r['case_id']}**: {r['text_length']} 字, comp={r['completeness']:.1f}, coverage={r['coverage_estimate']*100:.0f}%")
    else:
        md.append("- 当前 baseline 内无长文本案例（边缘条件之外的 case 多在 4000-6000 字之间）")
    md.append("")
    md.append("## 4. v3.1.6 验收对照基线")
    md.append("")
    md.append(f"- baseline avg completeness = **{avg_completeness:.1f}** → 验收 ≥80")
    md.append(f"- baseline avg coverage = **{avg_coverage*100:.1f}%** → 验收 ≥80%")
    md.append(f"- baseline 长文本风险 = **{len(over_long)}** → Sprint 1 必须真分块")

    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[diagnostic_capacity] 写出 {md_path}")
    print(f"[diagnostic_capacity] 写出 {json_path}")
    print(f"[diagnostic_capacity] 平均 completeness={avg_completeness:.1f}, coverage={avg_coverage*100:.1f}%")

    return {"total": total, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 0 baseline: capacity diagnostic")
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
        print("[diagnostic_capacity] 警告：缺少 PyYAML，meta.yaml 解析将跳过；text_length 用原文长度估算")

    analyze(runs_dir, datasets_dir, output_dir, args.case_regex)


if __name__ == "__main__":
    main()
