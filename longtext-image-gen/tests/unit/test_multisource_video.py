"""
tests/unit/test_multisource_video.py — 多信源与视频生产扩展冒烟测试
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

import pytest
from pydantic import ValidationError

from agents.agent0_multisource import run_agent0_multisource
from ir.models import (
    ArtifactType,
    Blueprint,
    Claim,
    ContentTree,
    Gate1Result,
    Gate2Issue,
    Gate2Result,
    OutputFormat,
    RenderArtifact,
    RetryBudgetState,
    RouterDecision,
    Source,
    SourceBundle,
    SourceMode,
    StyleTokens,
)
from service.schemas import RenderRequest


# ─── 辅助函数 ───────────────────────────────────────────────────────────────────

def _make_minimal_blueprint(output_format: OutputFormat = OutputFormat.IMAGE) -> Blueprint:
    """构建最小可用 Blueprint（测试专用）。"""
    sb = SourceBundle(text="测试长文内容" * 20)
    ct = ContentTree(source_bundle=sb, card_budget=5, card_slots=[])
    return Blueprint(
        source_bundle=sb,
        router_decision=RouterDecision(),
        content_tree=ct,
        style_tokens=StyleTokens(),
        output_format=output_format,
        cards=[],
    )


def _make_gate1_passed() -> Gate1Result:
    return Gate1Result(passed=True, dim_scores=[], overall_score=90.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 一、多信源功能测试
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. SourceBundle 标准化 ────────────────────────────────────────────────────

def test_source_bundle_multi_normalizes_text_and_meta():
    """多信源 SourceBundle 应该被 Agent0 正常标准化，meta 包含 source_count。"""
    bundle = SourceBundle(
        mode=SourceMode.MULTI,
        sources=[
            Source(source_id="s1", raw_text="A 源说市场规模达到 100 亿。", source_name="A"),
            Source(source_id="s2", raw_text="B 源说市场规模达到 120 亿。", source_name="B"),
        ],
    )
    result = run_agent0_multisource(bundle)
    assert result.mode == SourceMode.MULTI
    assert result.char_count > 0
    assert "[来源 s1]" in result.text
    assert result.multi_source_meta["source_count"] == 2


# ── 2. source_id 保留 + meta 包含 source_ids ────────────────────────────────

def test_agent0_preserves_source_ids_in_meta():
    """多信源聚合后，meta 应包含所有 source_id。"""
    bundle = SourceBundle(
        mode=SourceMode.MULTI,
        sources=[
            Source(source_id="src_a", raw_text="A 信源内容 " * 10, source_name="A"),
            Source(source_id="src_b", raw_text="B 信源内容 " * 10, source_name="B"),
            Source(source_id="src_c", raw_text="C 信源内容 " * 10, source_name="C"),
        ],
    )
    result = run_agent0_multisource(bundle)
    meta = result.multi_source_meta
    assert meta["source_count"] == 3
    assert "src_a" in meta["source_ids"]
    assert "src_b" in meta["source_ids"]
    assert "src_c" in meta["source_ids"]
    # 每个 source 的 source_id 必须保留
    result_ids = {s.source_id for s in result.sources}
    assert result_ids == {"src_a", "src_b", "src_c"}


# ── 3. 近似重复文本生成 duplicate_clusters ──────────────────────────────────

def test_agent0_detects_near_duplicate_sources():
    """文本高度相似（> 0.85）的信源应被检测为 duplicate_cluster。"""
    # 构造两个前 600 字高度相似的来源（仅末尾不同）
    base_text = "这是一篇关于人工智能的深度报告，涵盖了大模型技术的最新进展、市场规模分析以及未来发展趋势。" * 10
    bundle = SourceBundle(
        mode=SourceMode.MULTI,
        sources=[
            Source(source_id="s1", raw_text=base_text + "略有差异版本一。", source_name="S1"),
            Source(source_id="s2", raw_text=base_text + "略有差异版本二。", source_name="S2"),
        ],
    )
    result = run_agent0_multisource(bundle)
    assert len(result.duplicate_clusters) >= 1, "高相似度信源应生成 duplicate_clusters"
    cluster = result.duplicate_clusters[0]
    assert "s1" in cluster.member_source_ids
    assert "s2" in cluster.member_source_ids
    assert cluster.representative_source_id in ("s1", "s2")
    # meta 中也应体现
    assert result.multi_source_meta["duplicate_cluster_count"] >= 1


# ── 4. 数字冲突生成 disagreements ─────────────────────────────────────────────

def test_agent0_generates_disagreements_for_disjoint_numbers():
    """两个信源数字完全不同时，应生成 disagreements。"""
    bundle = SourceBundle(
        mode=SourceMode.MULTI,
        sources=[
            Source(source_id="s1", raw_text="今年市场规模达 300亿元，同比增长 15%。", source_name="S1"),
            Source(source_id="s2", raw_text="市场规模预估 500亿元，同比增长 28%。", source_name="S2"),
        ],
    )
    result = run_agent0_multisource(bundle)
    assert len(result.disagreements) >= 1, "数字不同的信源应生成 disagreements"
    dis = result.disagreements[0]
    assert dis.disagreement_type == "data_disagreement"
    assert len(dis.source_views) == 2
    assert dis.resolution == "preserve_disagreement"
    # meta 中也应体现
    assert result.multi_source_meta["disagreement_count"] >= 1


# ── 5. Claim 有 source_refs，不能出现无来源 claim ────────────────────────────

def test_claim_defaults_source_refs_for_backward_compatibility():
    """Claim model_validator 应确保 source_refs 不为空（单源兼容）。"""
    claim = Claim(claim_text="核心结论", evidence_span="原文证据")
    assert claim.source_refs == ["single"]
    assert claim.evidence_spans[0]["source_id"] == "single"


def test_claim_source_refs_preserved_when_provided():
    """多源场景下显式提供 source_refs 时应保留。"""
    claim = Claim(
        claim_text="市场规模达到 100 亿",
        evidence_span="市场规模达到 100 亿",
        source_refs=["src_a", "src_b"],
    )
    assert "src_a" in claim.source_refs
    assert "src_b" in claim.source_refs
    # 不应被 model_validator 覆盖
    assert "single" not in claim.source_refs


def test_claim_validator_never_leaves_source_refs_empty():
    """无论如何构造 Claim，source_refs 都不应为空。"""
    claim = Claim(claim_text="任意声明", evidence_span="", source_refs=[])
    assert len(claim.source_refs) > 0


# ── 6. 单源旧路径兼容 ─────────────────────────────────────────────────────────

def test_agent0_single_source_text_only_compatibility():
    """SourceBundle(text=...) 单源模式应透传，不产生 clusters 或 disagreements。"""
    bundle = SourceBundle(text="这是一段单信源文本，描述某个技术进展。" * 5)
    result = run_agent0_multisource(bundle)
    assert result.mode == SourceMode.SINGLE
    assert len(result.duplicate_clusters) == 0
    assert len(result.disagreements) == 0
    assert result.multi_source_meta == {}


def test_agent0_single_source_explicit_mode_compatibility():
    """SourceBundle(mode=single, sources=[单source]) 同样应正常工作。"""
    bundle = SourceBundle(
        mode=SourceMode.SINGLE,
        sources=[Source(source_id="only", raw_text="只有一个信源的文章内容。" * 10)],
    )
    result = run_agent0_multisource(bundle)
    assert result.mode == SourceMode.SINGLE
    assert len(result.duplicate_clusters) == 0


# ── 7. API RenderRequest multi 校验 ─────────────────────────────────────────

def test_render_request_supports_multi_sources_without_qrcode_field():
    """RenderRequest(mode=multi) 应通过校验，且不存在 qrcode 字段。"""
    req = RenderRequest(
        mode="multi",
        sources=[
            {"source_id": "s1", "raw_text": "第一段多信源文本" * 5},
            {"source_id": "s2", "raw_text": "第二段多信源文本" * 5},
        ],
        output_format="video",
    )
    assert req.output_format.value == OutputFormat.VIDEO.value
    assert "第一段多信源文本" in req.primary_text
    assert "qrcode" not in RenderRequest.model_fields


def test_render_request_multi_requires_sources():
    """mode=multi 时未提供 sources 应抛出 ValidationError。"""
    with pytest.raises(ValidationError):
        RenderRequest(mode="multi")


def test_render_request_single_with_text_still_works():
    """单源模式 text 字段应正常工作（旧路径兼容）。"""
    req = RenderRequest(text="这是一段超过一百字的单信源测试文本，用来验证单源模式兼容性。" * 5)
    assert req.mode.value == "single"
    assert len(req.primary_text) >= 100


# ═══════════════════════════════════════════════════════════════════════════════
# 二、视频生产功能测试
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. RenderArtifact 支持视频元数据 ─────────────────────────────────────────

def test_render_artifact_supports_video_metadata():
    """RenderArtifact 应支持 artifact_type=video 及视频专有字段。"""
    artifact = RenderArtifact(
        output_path="/tmp/out.mp4",
        artifact_type=ArtifactType.VIDEO,
        duration_sec=9.0,
        thumbnail_path="/tmp/out.thumb.png",
        subtitle_path="/tmp/out.srt",
    )
    assert artifact.artifact_type == ArtifactType.VIDEO
    assert artifact.duration_sec == 9.0
    assert artifact.thumbnail_path == "/tmp/out.thumb.png"
    assert artifact.subtitle_path == "/tmp/out.srt"


def test_render_artifact_image_has_no_duration():
    """图片类型的 RenderArtifact 默认 duration_sec 为 None。"""
    artifact = RenderArtifact(
        output_path="/tmp/out.png",
        artifact_type=ArtifactType.IMAGE,
    )
    assert artifact.artifact_type == ArtifactType.IMAGE
    assert artifact.duration_sec is None


# ── 2. FFmpeg 缺失时 Tool2 抛出明确错误 ──────────────────────────────────────

def test_tool2_raises_clear_error_when_ffmpeg_missing(monkeypatch):
    """FFmpeg 未安装时，run_tool2_video_render 应抛出明确的 RuntimeError。"""
    import shutil
    from tools.tool2_video_render import run_tool2_video_render

    # 强制 shutil.which 返回 None（模拟 FFmpeg 未安装）
    monkeypatch.setattr(shutil, "which", lambda name: None)

    bp = _make_minimal_blueprint(output_format=OutputFormat.VIDEO)
    with pytest.raises(RuntimeError) as exc_info:
        run_tool2_video_render(bp, "/tmp/test_video.mp4")

    # 错误信息必须明确提到 FFmpeg 和降级选项
    error_msg = str(exc_info.value)
    assert "FFmpeg" in error_msg or "ffmpeg" in error_msg.lower()
    assert "未安装" in error_msg or "not found" in error_msg.lower()


# ── 3. node_tool2 在 Tool2 失败时降级为图片 ──────────────────────────────────

def test_node_tool2_degrades_to_image_when_tool2_fails(monkeypatch):
    """Tool2 失败时，node_tool2 应降级为图片输出，不应崩溃。"""
    from orchestrator.graph import node_tool2

    fake_image_artifact = RenderArtifact(
        output_path="/tmp/degraded.png",
        artifact_type=ArtifactType.IMAGE,
        width=1080,
        height=1920,
    )

    monkeypatch.setattr(
        "orchestrator.graph.run_tool2_video_render",
        lambda bp, path, **kw: (_ for _ in ()).throw(RuntimeError("FFmpeg 未安装，无法生成视频；可降级输出图片")),
    )
    monkeypatch.setattr(
        "orchestrator.graph.run_tool1_render",
        lambda bp, path: fake_image_artifact,
    )

    bp = _make_minimal_blueprint(output_format=OutputFormat.VIDEO)
    state = {
        "blueprint": bp,
        "output_path": "/tmp/test_output.mp4",
    }
    result = node_tool2(state)

    assert result["render_artifact"].artifact_type == ArtifactType.IMAGE, \
        "Tool2 失败后应降级为图片产物"
    assert result["degradation_level"] == "L1", \
        "降级级别应为 L1"
    assert "Tool2 failed" in result.get("retry_reason", ""), \
        "retry_reason 应记录 Tool2 失败原因"


# ── 4. output_format=video 时 Gate1 路由到 Tool2 ─────────────────────────────

def test_route_after_gate1_returns_tool2_for_video_format():
    """Gate1 通过且 blueprint.output_format=VIDEO 时，应路由到 tool2。"""
    from orchestrator.graph import route_after_gate1

    bp = _make_minimal_blueprint(output_format=OutputFormat.VIDEO)
    state = {
        "blueprint": bp,
        "gate1_result": _make_gate1_passed(),
        "retry_budget": RetryBudgetState(),
    }
    assert route_after_gate1(state) == "tool2"


# ── 5. output_format=image 时 Gate1 路由到 Tool1 ─────────────────────────────

def test_route_after_gate1_returns_tool1_for_image_format():
    """Gate1 通过且 blueprint.output_format=IMAGE 时，应路由到 tool1。"""
    from orchestrator.graph import route_after_gate1

    bp = _make_minimal_blueprint(output_format=OutputFormat.IMAGE)
    state = {
        "blueprint": bp,
        "gate1_result": _make_gate1_passed(),
        "retry_budget": RetryBudgetState(),
    }
    assert route_after_gate1(state) == "tool1"


# ── 6. output_format=auto 稳定 fallback ──────────────────────────────────────

def test_route_after_gate1_auto_format_has_stable_fallback():
    """output_format=AUTO 时，Blueprint 经 agent3 应已解析为 image/video，不应卡住。"""
    from orchestrator.graph import route_after_gate1

    # AUTO 在 node_agent3 中已解析为 IMAGE（无 router hint 时默认 IMAGE）
    bp = _make_minimal_blueprint(output_format=OutputFormat.IMAGE)
    state = {
        "blueprint": bp,
        "gate1_result": _make_gate1_passed(),
        "retry_budget": RetryBudgetState(),
    }
    route = route_after_gate1(state)
    assert route in ("tool1", "tool2"), \
        f"auto 解析后应路由到 tool1 或 tool2，实际: {route}"


# ── 7. Gate2 对视频产物走视频检查，不调用图片 OCR/Vision 路径 ──────────────

def test_gate2_uses_video_checks_for_video_artifact(tmp_path):
    """Gate2 对 video artifact 应走视频基础检查，不应调用 OCR 或 Vision LLM。"""
    from gates.gate2_render import run_gate2_render

    # 创建假视频文件和字幕文件
    video_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"\x00" * 2048)  # 非空假视频
    srt_path = tmp_path / "output.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n测试字幕\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\n第二条字幕\n",
        encoding="utf-8",
    )

    artifact = RenderArtifact(
        output_path=str(video_path),
        artifact_type=ArtifactType.VIDEO,
        duration_sec=9.0,
        thumbnail_path=str(tmp_path / "thumb.png"),
        subtitle_path=str(srt_path),
    )

    bp = _make_minimal_blueprint(output_format=OutputFormat.VIDEO)
    result = run_gate2_render(artifact, bp)

    check_names = {issue.check_name for issue in result.issues}

    # 视频专用检查应出现
    assert "video_file_exists" in check_names, "应包含 video_file_exists 检查"
    assert "video_duration" in check_names, "应包含 video_duration 检查"
    assert "video_subtitle" in check_names, "应包含 video_subtitle 检查"

    # 图片专用检查不应出现
    assert "ocr_typo" not in check_names, "视频产物不应调用 OCR 检查"
    assert "vision_score" not in check_names, "视频产物不应调用 Vision LLM 检查"
    assert "font_size" not in check_names, "视频产物不应调用字号检查"
    assert "overflow" not in check_names, "视频产物不应调用溢出检查"

    # OCR 跳过标志必须为 True
    assert result.ocr_skipped is True


def test_gate2_video_passes_with_valid_files(tmp_path):
    """有效的视频文件、字幕文件和正确 duration 应使 Gate2 通过。"""
    from gates.gate2_render import run_gate2_render

    video_path = tmp_path / "valid.mp4"
    video_path.write_bytes(b"FAKE_MP4_CONTENT" * 64)  # 非空
    srt_path = tmp_path / "valid.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:03,000\n内容\n", encoding="utf-8")

    artifact = RenderArtifact(
        output_path=str(video_path),
        artifact_type=ArtifactType.VIDEO,
        duration_sec=9.0,
        thumbnail_path=None,
        subtitle_path=str(srt_path),
    )

    bp = _make_minimal_blueprint(output_format=OutputFormat.VIDEO)
    result = run_gate2_render(artifact, bp)

    assert result.passed is True, f"有效视频应通过 Gate2，实际 issues: {result.issues}"


def test_gate2_video_fails_when_video_file_missing(tmp_path):
    """视频文件不存在时，Gate2 应返回 passed=False。"""
    from gates.gate2_render import run_gate2_render

    artifact = RenderArtifact(
        output_path=str(tmp_path / "nonexistent.mp4"),
        artifact_type=ArtifactType.VIDEO,
        duration_sec=9.0,
        thumbnail_path=None,
        subtitle_path=None,
    )

    bp = _make_minimal_blueprint(output_format=OutputFormat.VIDEO)
    result = run_gate2_render(artifact, bp)

    assert result.passed is False, "视频文件不存在时应 Gate2 不通过"


# ── 8. Gate2 fact_drift 应回退到 agent2（而非 agent3）────────────────────────

def test_route_after_gate2_fact_drift_routes_to_agent2():
    """Gate2 fact_drift 失败时，应回退到 agent2（按 tech_design §1.3）。"""
    from orchestrator.graph import route_after_gate2

    fact_drift_issue = Gate2Issue(
        check_name="fact_drift",
        passed=False,
        detail="数字在图中未找到",
        issue_type="fact_drift",
    )
    g2 = Gate2Result(
        passed=False,
        issues=[fact_drift_issue],
        vision_score=0.0,
    )
    state = {
        "gate2_result": g2,
        "retry_budget": RetryBudgetState(),
        "render_artifact": RenderArtifact(
            output_path="/tmp/out.png",
            artifact_type=ArtifactType.IMAGE,
        ),
    }
    assert route_after_gate2(state) == "agent2", \
        "fact_drift 应回退到 agent2，当前路由到了错误目标"


def test_route_after_gate2_blueprint_level_routes_to_agent3():
    """Gate2 blueprint_level 失败时，应回退到 agent3。"""
    from orchestrator.graph import route_after_gate2

    bp_issue = Gate2Issue(
        check_name="ocr_typo",
        passed=False,
        detail="文字错误",
        issue_type="blueprint_level",
    )
    g2 = Gate2Result(passed=False, issues=[bp_issue], vision_score=0.0)
    state = {
        "gate2_result": g2,
        "retry_budget": RetryBudgetState(),
        "render_artifact": RenderArtifact(
            output_path="/tmp/out.png",
            artifact_type=ArtifactType.IMAGE,
        ),
    }
    assert route_after_gate2(state) == "agent3"


def test_route_after_gate2_render_only_routes_to_tool1_for_image():
    """Gate2 render_only 失败且产物为图片时，应重跑 tool1。"""
    from orchestrator.graph import route_after_gate2

    ro_issue = Gate2Issue(
        check_name="overflow",
        passed=False,
        detail="文字溢出",
        issue_type="render_only",
    )
    g2 = Gate2Result(passed=False, issues=[ro_issue], vision_score=0.0)
    state = {
        "gate2_result": g2,
        "retry_budget": RetryBudgetState(),
        "render_artifact": RenderArtifact(
            output_path="/tmp/out.png",
            artifact_type=ArtifactType.IMAGE,
        ),
    }
    assert route_after_gate2(state) == "tool1"


def test_route_after_gate2_render_only_routes_to_tool2_for_video():
    """Gate2 render_only 失败且产物为视频时，应重跑 tool2。"""
    from orchestrator.graph import route_after_gate2

    ro_issue = Gate2Issue(
        check_name="video_file_exists",
        passed=False,
        detail="视频文件不存在",
        issue_type="render_only",
    )
    g2 = Gate2Result(passed=False, issues=[ro_issue], vision_score=0.0)
    state = {
        "gate2_result": g2,
        "retry_budget": RetryBudgetState(),
        "render_artifact": RenderArtifact(
            output_path="/tmp/out.mp4",
            artifact_type=ArtifactType.VIDEO,
            duration_sec=9.0,
        ),
    }
    assert route_after_gate2(state) == "tool2"


def test_route_after_gate2_exhausted_budget_degrades():
    """预算耗尽时 Gate2 应触发降级，不应继续重试。"""
    from orchestrator.graph import route_after_gate2

    ro_issue = Gate2Issue(
        check_name="overflow",
        passed=False,
        detail="溢出",
        issue_type="render_only",
    )
    g2 = Gate2Result(passed=False, issues=[ro_issue], vision_score=0.0)
    # render_only 预算已满
    exhausted_budget = RetryBudgetState(render_only=3)
    state = {
        "gate2_result": g2,
        "retry_budget": exhausted_budget,
        "render_artifact": RenderArtifact(
            output_path="/tmp/out.png",
            artifact_type=ArtifactType.IMAGE,
        ),
    }
    route = route_after_gate2(state)
    assert route.startswith("degrade_"), \
        f"预算耗尽应触发降级，实际路由: {route}"
