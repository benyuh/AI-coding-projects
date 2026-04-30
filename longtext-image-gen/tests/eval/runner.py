"""
tests/eval/runner.py — 评估运行器（v3.1.5）

负责：
1. 读取 evals/datasets/{category} 下的用例（.txt + .meta.yaml）。
2. 调用 orchestrator/graph.py 的 run_pipeline_with_gates。
3. 将全量 IO 归档至 evals/runs/{run_id}/{case_id}/。
4. 生成 manifest.yaml 与 summary.csv。
"""

import json
import os
import shutil
import time
import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel

from orchestrator.graph import run_pipeline_with_gates
from ir.models import SourceBundle, OutputFormat, RouterDecision


class CaseMeta(BaseModel):
    case_id: str
    category: str
    sub_category: Optional[str] = None
    title: str
    source_url: Optional[str] = None
    text_length: Optional[Any] = None
    language: str = "zh"
    source_count: int = 1
    expected_behavior: Optional[Dict[str, Any]] = None
    preferences: Optional[Dict[str, Any]] = None
    user_profile: Optional[Dict[str, Any]] = None
    tags: List[str] = []


class EvalRunner:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.evals_dir = self.project_root / "evals"
        self.datasets_dir = self.evals_dir / "datasets"
        self.runs_dir = self.evals_dir / "runs"
        self.reports_dir = self.evals_dir / "reports"

        # 确保目录存在
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def run_category(self, category: str, max_cases: Optional[int] = None) -> str:
        """运行特定类别的所有用例。"""
        category_dir = self.datasets_dir / category
        if not category_dir.exists():
            raise ValueError(f"Category directory not found: {category_dir}")

        run_id = f"{datetime.now().strftime('%Y%m%d_%H%M')}_{category}"
        current_run_dir = self.runs_dir / run_id
        current_run_dir.mkdir(parents=True, exist_ok=True)

        print(f"🚀 开始评估运行: {run_id}")
        
        # 加载用例
        cases = self._load_cases(category_dir, category)
        if max_cases:
            cases = cases[:max_cases]
        print(f"📊 发现 {len(cases)} 个用例")

        summary_data = []
        manifest = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "dataset": category,
            "cases_total": len(cases),
            "cases_succeeded": 0,
            "cases_degraded": 0,
            "cases_rejected": 0,
            "total_duration_sec": 0,
            "total_tokens": {"sonnet_in": 0, "sonnet_out": 0, "haiku_in": 0, "haiku_out": 0},
            "notes": "",
        }

        start_time_all = time.time()

        for case_id, meta, text_content, multi_sources in cases:
            print(f"─── 正在运行用例: {case_id} ({meta.title}) ───")
            case_output_dir = current_run_dir / case_id
            case_output_dir.mkdir(parents=True, exist_ok=True)
            (case_output_dir / "stages").mkdir(exist_ok=True)
            (case_output_dir / "artifacts").mkdir(exist_ok=True)

            # 保存 input.json
            input_data = {
                "text": text_content,
                "multi_sources": multi_sources,
                "preferences": meta.preferences,
            }
            with open(case_output_dir / "input.json", "w", encoding="utf-8") as f:
                json.dump(input_data, f, ensure_ascii=False, indent=2)

            # 准备参数
            output_format_str = meta.preferences.get("output_format", "auto") if meta.preferences else "auto"
            try:
                output_format = OutputFormat(output_format_str)
            except ValueError:
                output_format = OutputFormat.AUTO

            # 从 meta.preferences 读取 target_platform（v3.1.6）
            target_platform: Optional[str] = None
            if meta.preferences:
                target_platform = meta.preferences.get("target_platform") or None

            # 决定后缀
            ext = ".png"
            if output_format == OutputFormat.VIDEO:
                ext = ".mp4"
            # 如果是 AUTO，由于我们不知道最终会分流到哪，这里先给一个基础名，但 Playwright 截图需要后缀
            # 实际上 orchestrator 会处理最终路径，但传递进去的 output_path 最好带上期望的后缀
            target_path = str(case_output_dir / "artifacts" / f"output{ext}")

            # 运行 Pipeline
            from infra.tracing import start_trace_capture
            case_start_time = time.time()
            trace_ctx = start_trace_capture()

            try:
                from ir.models import Source, SourceMode

                if multi_sources:
                    source_bundle = SourceBundle(
                        mode=SourceMode.MULTI,
                        sources=[Source(source_id=f"s{i+1}", raw_text=t) for i, t in enumerate(multi_sources)],
                    )
                    final_output_path, final_state = run_pipeline_with_gates(
                        text=None,
                        source_bundle=source_bundle,
                        output_path=target_path,
                        output_format=output_format,
                        target_platform=target_platform,
                    )
                else:
                    final_output_path, final_state = run_pipeline_with_gates(
                        text=text_content,
                        output_path=target_path,
                        output_format=output_format,
                        target_platform=target_platform,
                    )
                case_duration = time.time() - case_start_time

                # 保存 trace.jsonl
                self._save_trace_jsonl(case_output_dir, trace_ctx)

                # 归档结果
                self._archive_case_result(case_output_dir, final_state, final_output_path)

                # 累加 token (从 trace 中提取)
                self._aggregate_tokens(manifest, trace_ctx)

                # ── 真实性硬校验（防 fallback 假通过）────────────────────────
                total_in = sum(c.get("tokens_in", 0) for ev in trace_ctx.events for c in ev.model_calls)
                total_out = sum(c.get("tokens_out", 0) for ev in trace_ctx.events for c in ev.model_calls)
                realness_issues = []
                if total_in == 0:
                    realness_issues.append("token_in=0 (LLM 未调通)")
                if case_duration < 15:
                    realness_issues.append(f"耗时仅 {case_duration:.1f}s (疑似 fallback)")
                # 视频断言：有 video 期望但产物无 .mp4 时标注（不阻塞）
                if output_format == OutputFormat.VIDEO:
                    artifacts_found = list((case_output_dir / "artifacts").glob("*"))
                    has_mp4 = any(a.suffix == ".mp4" for a in artifacts_found)
                    if not has_mp4:
                        realness_issues.append("format=video 但产物无 .mp4 (L1 降级)")
                if realness_issues:
                    print(f"⚠️  [realness] {case_id} 真实性问题: {'; '.join(realness_issues)}")
                # ─────────────────────────────────────────────────────────────

                # 收集汇总信息
                if realness_issues and total_in == 0:
                    # token=0 是最严重的 fallback，标 invalid
                    status = "invalid"
                    manifest["cases_degraded"] += 1
                elif final_state.get("degradation_level"):
                    status = "degraded"
                    manifest["cases_degraded"] += 1
                else:
                    status = "passed"
                    manifest["cases_succeeded"] += 1

                summary_data.append(self._build_summary_row(
                    case_id, final_state, case_duration, realness_issues
                ))

            except Exception as e:
                case_duration = time.time() - case_start_time
                print(f"❌ 用例 {case_id} 失败: {e}")
                import traceback
                traceback.print_exc()

                with open(case_output_dir / "error.txt", "w", encoding="utf-8") as f:
                    f.write(str(e))
                    f.write("\n")
                    import traceback
                    traceback.print_exc(file=f)

                summary_data.append({
                    "case_id": case_id,
                    "terminal_status": "error",
                    "duration_s": f"{case_duration:.2f}",
                    "error": str(e),
                    "realness_check": "error",
                })

            # 防 oneAPI QPS 限流：case 间间隔 2s
            time.sleep(2)

        manifest["total_duration_sec"] = int(time.time() - start_time_all)

        # 保存 manifest 和 summary
        with open(current_run_dir / "manifest.yaml", "w", encoding="utf-8") as f:
            yaml.dump(manifest, f, allow_unicode=True)
            
        with open(current_run_dir / "summary.csv", "w", encoding="utf-8", newline="") as f:
            if summary_data:
                # 获取所有行中出现过的所有 key 作为 fieldnames
                fieldnames = []
                for row in summary_data:
                    for key in row.keys():
                        if key not in fieldnames:
                            fieldnames.append(key)
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(summary_data)

        print(f"✅ 运行完成。结果归档至: {current_run_dir}")
        return run_id

    def _load_cases(self, category_dir: Path, category: str) -> List[tuple]:
        """加载目录下的所有用例。"""
        cases = []
        # 寻找所有的 .meta.yaml 文件
        meta_files = list(category_dir.glob("*.meta.yaml"))
        for meta_file in meta_files:
            case_id = meta_file.name.replace(".meta.yaml", "")
            with open(meta_file, "r", encoding="utf-8") as f:
                meta_dict = yaml.safe_load(f)
                if not meta_dict:
                    print(f"⚠️ 跳过空 meta 文件: {meta_file.name}")
                    continue
                meta_dict["case_id"] = case_id
                meta_dict["category"] = category
                try:
                    meta = CaseMeta(**meta_dict)
                except Exception as e:
                    print(f"⚠️ 跳过无效 meta 文件: {meta_file.name} - {e}")
                    continue
            
            # 寻找对应的文本文件
            txt_file = category_dir / f"{case_id}.txt"
            text_content = ""
            multi_sources = []
            
            if txt_file.exists():
                with open(txt_file, "r", encoding="utf-8") as f:
                    text_content = f.read()
            else:
                # 可能是多源场景，检查是否有一组文件
                # 比如 func_07_multi_source.meta.yaml 对应 func_07_multi_source_s1.txt 等
                source_files = list(category_dir.glob(f"{case_id}_s*.txt"))
                if source_files:
                    for sf in sorted(source_files):
                        with open(sf, "r", encoding="utf-8") as f:
                            multi_sources.append(f.read())
                    text_content = "\n\n---\n\n".join(multi_sources)
            
            cases.append((case_id, meta, text_content, multi_sources))
        
        return sorted(cases, key=lambda x: x[0])

    def _archive_case_result(self, case_dir: Path, state: Dict[str, Any], output_path: str):
        """保存阶段性数据和产物。"""
        # 1. 保存 result.json (精简后的最终状态)
        result_data = {
            "status": "degraded" if state.get("degradation_level") else "passed",
            "degradation_level": state.get("degradation_level"),
            "output_path": output_path,
            "error": state.get("error"),
        }
        with open(case_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        # 2. 保存 blueprint.json
        blueprint = state.get("blueprint")
        if blueprint:
            # 假设 Blueprint 是 Pydantic 模型
            with open(case_dir / "blueprint.json", "w", encoding="utf-8") as f:
                if hasattr(blueprint, "model_dump_json"):
                    f.write(blueprint.model_dump_json(indent=2))
                else:
                    json.dump(str(blueprint), f)

        # 3. 保存各个 stage 的输出
        stages_dir = case_dir / "stages"
        stage_keys = [
            ("router_decision", "agent1_router.json"),
            ("content_tree", "agent2_understanding.json"),
            ("gate1_result", "gate1.json"),
            ("gate2_result", "gate2.json"),
            ("render_artifact", "tool_render.json"),
        ]
        for key, filename in stage_keys:
            data = state.get(key)
            if data:
                with open(stages_dir / filename, "w", encoding="utf-8") as f:
                    if hasattr(data, "model_dump_json"):
                        f.write(data.model_dump_json(indent=2))
                    else:
                        json.dump(str(data), f)

        # 4. 移动产物到 artifacts 目录
        if output_path and os.path.exists(output_path):
            file_ext = os.path.splitext(output_path)[1]
            dest_name = f"output{file_ext}"
            dest_path = case_dir / "artifacts" / dest_name
            if Path(output_path).resolve() != Path(dest_path).resolve():
                shutil.copy(output_path, dest_path)
            
            # 如果是视频，可能还有辅助文件（字幕等）
            if file_ext == ".mp4":
                # 尝试寻找同名的 .ass 或 .srt
                ass_path = output_path.replace(".mp4", ".ass")
                if os.path.exists(ass_path):
                    shutil.copy(ass_path, case_dir / "artifacts" / "subtitles.ass")

    def _build_summary_row(
        self,
        case_id: str,
        state: Dict[str, Any],
        duration: float,
        realness_issues: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """构建 summary.csv 的一行。"""
        rd: Optional[RouterDecision] = state.get("router_decision")
        g1 = state.get("gate1_result")
        g2 = state.get("gate2_result")
        artifact = state.get("render_artifact")
        realness_issues = realness_issues or []

        if realness_issues and any("token_in=0" in i for i in realness_issues):
            terminal_status = "invalid"
        elif state.get("terminal_status"):
            # 优先使用 orchestrator 写入的精确状态（l1_degraded/l2_degraded/l3_degraded/blocked/passed）
            terminal_status = state["terminal_status"]
        elif state.get("degradation_level"):
            terminal_status = "degraded"  # legacy fallback
        else:
            terminal_status = "passed"

        return {
            "case_id": case_id,
            "terminal_status": terminal_status,
            "format": rd.output_format_hint.value if rd else "unknown",
            "platform": rd.platform.value if rd else "unknown",
            "style_id": state.get("blueprint").style_tokens.style_id if state.get("blueprint") else "unknown",
            "narrative_structure": rd.narrative_structure.value if rd else "unknown",
            "card_count": len(state.get("blueprint").cards) if state.get("blueprint") else 0,
            "gate1_pass": g1.passed if g1 else "n/a",
            "gate2_pass": g2.passed if g2 else "n/a",
            "duration_s": f"{duration:.2f}",
            "degrade_level": state.get("degradation_level", ""),
            "realness_check": "; ".join(realness_issues) if realness_issues else "ok",
        }

    def _save_trace_jsonl(self, case_dir: Path, ctx: Any):
        """保存 trace.jsonl。"""
        import dataclasses
        trace_path = case_dir / "trace.jsonl"
        with open(trace_path, "w", encoding="utf-8") as f:
            for event in ctx.events:
                # 只保存有意义的 stage (过滤掉内部工具调用等，如果装饰器用得太细)
                row = dataclasses.asdict(event)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _aggregate_tokens(self, manifest: Dict[str, Any], ctx: Any):
        """从 Trace 中累加 Token 消耗。"""
        for event in ctx.events:
            for call in event.model_calls:
                model = call.get("model", "")
                if "sonnet" in model.lower():
                    manifest["total_tokens"]["sonnet_in"] += call.get("tokens_in", 0)
                    manifest["total_tokens"]["sonnet_out"] += call.get("tokens_out", 0)
                elif "haiku" in model.lower():
                    manifest["total_tokens"]["haiku_in"] += call.get("tokens_in", 0)
                    manifest["total_tokens"]["haiku_out"] += call.get("tokens_out", 0)
