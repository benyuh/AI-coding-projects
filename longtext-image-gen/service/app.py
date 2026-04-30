"""
service/app.py — FastAPI 服务层（v3.1.5）

端点：
  POST /render          异步提交渲染任务，返回 job_id
  GET  /status/{job_id} 查询任务状态和结果
  GET  /health          健康检查

设计原则：
- 每个请求在独立线程池中执行（concurrent.futures）
- 任务状态写入 InMemoryJobStore（线程安全）
- 不阻塞 FastAPI 事件循环
- 最终输出 PNG 写入 service/outputs/ 目录
"""
from __future__ import annotations

import concurrent.futures
import datetime
import pathlib
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Path
from fastapi.responses import FileResponse

from service.job_store import InMemoryJobStore, get_job_store
from service.schemas import (
    HealthResponse,
    JobResult,
    JobStatus,
    RenderRequest,
    RenderResponse,
)

_OUTPUTS_DIR = pathlib.Path(__file__).parent.parent / "service" / "outputs"
_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# 线程池在 lifespan 中创建和销毁，避免跨 TestClient 实例共享状态
_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _run_render_job(job_id: str, req: RenderRequest) -> None:
    """
    在线程池中执行完整 Pipeline，将结果写回 JobStore。
    此函数运行在非 asyncio 线程中，可以安全地调用同步 API。
    """
    store = get_job_store()
    store.update(job_id, status=JobStatus.RUNNING)

    try:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(_OUTPUTS_DIR / f"{job_id[:8]}_{timestamp}.png")

        # 动态 import 避免循环依赖（service 层不持有 main.py 引用）
        from ir.models import OutputFormat as IROutputFormat, Source, SourceAuthority, SourceBundle, SourceMode
        from main import run_pipeline

        source_bundle = None
        if req.mode.value == "multi":
            source_bundle = SourceBundle(
                mode=SourceMode.MULTI,
                sources=[
                    Source(
                        source_id=s.source_id,
                        raw_text=s.raw_text,
                        source_name=s.source_name,
                        url=s.url,
                        title=s.title,
                        publish_time=s.publish_time,
                        authority=SourceAuthority(s.authority),
                    )
                    for s in req.sources
                ],
            )

        result_path = run_pipeline(
            text=req.primary_text,
            output_path=output_path,
            save_blueprint=req.save_blueprint,
            use_gates=req.use_gates,
            source_bundle=source_bundle,
            output_format=IROutputFormat(req.output_format.value),
        )

        # 获取渲染元数据（从文件读取，不依赖内部对象）
        out = pathlib.Path(result_path)
        file_size_kb = out.stat().st_size // 1024 if out.exists() else 0

        store.update(
            job_id,
            status=JobStatus.DONE,
            output_path=result_path,
            artifact_type="video" if result_path.endswith(".mp4") else "image",
            file_size_kb=file_size_kb,
        )

    except Exception as exc:
        store.update(job_id, status=JobStatus.FAILED, error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan：初始化 JobStore 和线程池。"""
    global _executor
    _executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="render")
    get_job_store()  # 确保单例初始化
    yield
    _executor.shutdown(wait=False)
    _executor = None


app = FastAPI(
    title="LongText → 信息图 API",
    version="3.1.5",
    description="将长文转换为小红书/微信朋友圈信息图 PNG，支持异步渲染任务。",
    lifespan=lifespan,
)


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["运维"])
async def health_check() -> HealthResponse:
    """服务健康检查。"""
    return HealthResponse()


@app.post("/render", response_model=RenderResponse, status_code=202, tags=["渲染"])
async def submit_render(
    req: Annotated[RenderRequest, Body()],
) -> RenderResponse:
    """
    提交渲染任务（异步）。

    - 立即返回 `job_id`，客户端用 `GET /status/{job_id}` 轮询结果。
    - 任务在后台线程中执行完整 Pipeline（Agent1→Agent2→Agent3→Gate1→Tool1→Gate2）。
    """
    store = get_job_store()
    job_id = store.create()

    # 提交到线程池（非阻塞）
    if _executor is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")
    _executor.submit(_run_render_job, job_id, req)

    return RenderResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="渲染任务已提交，请用 GET /status/{job_id} 查询进度",
    )


@app.get("/status/{job_id}", response_model=JobResult, tags=["渲染"])
async def get_status(
    job_id: Annotated[str, Path(description="由 POST /render 返回的 job_id")],
) -> JobResult:
    """查询渲染任务状态和结果。"""
    store = get_job_store()
    result = store.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return result


@app.get("/download/{job_id}", tags=["渲染"])
async def download_png(
    job_id: Annotated[str, Path(description="已完成的渲染任务 job_id")],
):
    """
    下载已完成的渲染 PNG 文件。
    任务状态必须为 `done`，否则返回 409。
    """
    store = get_job_store()
    result = store.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if result.status != JobStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"任务状态为 {result.status}，尚未完成",
        )
    png_path = pathlib.Path(result.output_path)
    if not png_path.exists():
        raise HTTPException(status_code=410, detail="输出文件已被清理")
    return FileResponse(
        path=str(png_path),
        media_type="image/png",
        filename=png_path.name,
    )
