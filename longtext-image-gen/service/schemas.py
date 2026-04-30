"""
service/schemas.py — FastAPI 请求/响应 Pydantic 模型（v3.1.5）
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class InputMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class OutputFormat(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUTO = "auto"


class SourceInput(BaseModel):
    source_id: str = Field(..., description="信源 ID，如 s1")
    raw_text: str = Field(..., min_length=20, description="该信源原文")
    source_name: str = Field("", description="信源名称，可选")
    url: Optional[str] = Field(None, description="原文链接，可选")
    title: str = Field("", description="信源标题，可选")
    publish_time: Optional[str] = Field(None, description="发布时间，可选")
    authority: str = Field("general_web", description="信源权威性")


class RenderRequest(BaseModel):
    text: Optional[str] = Field(None, min_length=100, description="单信源长文（≥100字）")
    mode: InputMode = Field(InputMode.SINGLE, description="输入模式：single 或 multi")
    content: Optional[str] = Field(None, min_length=100, description="单信源内容，兼容 tech_design 的 input.content")
    sources: list[SourceInput] = Field(default_factory=list, description="多信源输入列表")
    output_format: OutputFormat = Field(OutputFormat.IMAGE, description="输出格式：image/video/auto")
    style_id: Optional[str] = Field(None, description="风格 ID（可选，如 tech_minimal / magazine_editorial）")
    save_blueprint: bool = Field(False, description="是否在响应中返回 Blueprint JSON")
    use_gates: bool = Field(True, description="是否启用 Gate 1/2 质量闭环")

    @model_validator(mode="after")
    def validate_input_payload(self) -> "RenderRequest":
        if self.mode == InputMode.MULTI:
            if not self.sources:
                raise ValueError("mode=multi 时必须提供 sources[]")
            return self
        if not (self.text or self.content):
            raise ValueError("mode=single 时必须提供 text 或 content")
        return self

    @property
    def primary_text(self) -> str:
        if self.mode == InputMode.MULTI:
            return "\n\n".join(s.raw_text for s in self.sources)
        return self.text or self.content or ""


class RenderResponse(BaseModel):
    job_id: str = Field(..., description="异步任务 ID")
    status: JobStatus = Field(JobStatus.PENDING)
    message: str = ""


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    output_path: Optional[str] = None
    artifact_type: str = "image"
    file_size_kb: Optional[int] = None
    render_time_s: Optional[float] = None
    duration_sec: Optional[float] = None
    thumbnail_path: Optional[str] = None
    degradation_level: Optional[str] = None   # "" | "L1" | "L2" | "L3"
    blueprint: Optional[dict] = None           # save_blueprint=True 时填充
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "3.1.5"
