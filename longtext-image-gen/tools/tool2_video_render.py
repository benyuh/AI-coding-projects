"""
tools/tool2_video_render.py — Tool 2 视频渲染（v3.2 MVP）

MVP 策略：
- 复用 Tool1 先生成一张信息图 PNG
- 生成基础 SRT 字幕文件
- 使用 FFmpeg 将图片循环成 MP4 静音视频
- 未安装 FFmpeg 时抛出明确错误，由上层降级为图片输出
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import time

from infra.tracing import trace
from ir.models import ArtifactType, Blueprint, RenderArtifact
from tools.tool1_image_render import run_tool1_render


def _format_srt_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    total = int(seconds)
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    return f"{hrs:02}:{mins:02}:{secs:02},{millis:03}"


def _build_subtitle_text(blueprint: Blueprint, subtitle_path: pathlib.Path, seconds_per_card: float) -> None:
    lines: list[str] = []
    for index, card in enumerate(blueprint.cards, start=1):
        start = (index - 1) * seconds_per_card
        end = index * seconds_per_card
        text_parts = [card.content.title]
        if card.content.body:
            text_parts.append(card.content.body)
        text = "\n".join(part for part in text_parts if part).strip() or f"第 {index} 张卡片"
        lines.extend([
            str(index),
            f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}",
            text,
            "",
        ])
    subtitle_path.write_text("\n".join(lines), encoding="utf-8")


@trace("Tool2.VideoRender")
def run_tool2_video_render(
    blueprint: Blueprint,
    output_path: str,
    seconds_per_card: float = 3.0,
) -> RenderArtifact:
    """从 Blueprint 生成 MP4 视频产物。"""
    t_start = time.time()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg 未安装，无法生成视频；可降级输出图片")

    video_path = pathlib.Path(output_path)
    if video_path.suffix.lower() != ".mp4":
        video_path = video_path.with_suffix(".mp4")
    video_path.parent.mkdir(parents=True, exist_ok=True)

    duration = max(len(blueprint.cards), 1) * seconds_per_card
    with tempfile.TemporaryDirectory(prefix="longtext_video_") as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        frame_path = tmp / "frame.png"
        subtitle_path = video_path.with_suffix(".srt")
        thumbnail_path = video_path.with_suffix(".thumb.png")

        image_artifact = run_tool1_render(blueprint, str(frame_path))
        _build_subtitle_text(blueprint, subtitle_path, seconds_per_card)
        shutil.copyfile(frame_path, thumbnail_path)

        cmd = [
            ffmpeg,
            "-y",
            "-loop", "1",
            "-i", str(frame_path),
            "-t", f"{duration:.2f}",
            "-vf", "scale=1080:-2",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(video_path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg 视频合成失败: {result.stderr[-500:]}")

    file_size_kb = video_path.stat().st_size // 1024 if video_path.exists() else 0
    return RenderArtifact(
        output_path=str(video_path),
        artifact_type=ArtifactType.VIDEO,
        file_size_kb=file_size_kb,
        render_time_s=time.time() - t_start,
        blueprint_id=blueprint.blueprint_id,
        width=image_artifact.width,
        height=image_artifact.height,
        card_count=len(blueprint.cards),
        duration_sec=duration,
        thumbnail_path=str(thumbnail_path),
        subtitle_path=str(subtitle_path),
    )
