"""
infra/tracing.py — @trace 装饰器与 Trace 采集机制（v3.1.5）

支持：
1. 打印耗时日志（兼容旧版本）。
2. 通过 contextvars 采集结构化 Trace 事件，供评估工具使用。
"""

import contextvars
import functools
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# ── Trace 数据结构 ───────────────────────────────────────────────────────────

@dataclass
class TraceEvent:
    stage: str
    started_at: str
    duration_ms: float = 0.0
    status: str = "pending"
    input_data: Any = None
    output_data: Any = None
    model_calls: List[Dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    error: Optional[str] = None

class TraceContext:
    def __init__(self):
        self.events: List[TraceEvent] = []
        self.current_event: Optional[TraceEvent] = None

    def add_event(self, event: TraceEvent):
        self.events.append(event)

# ── Context 管理 ─────────────────────────────────────────────────────────────

_trace_ctx = contextvars.ContextVar("_trace_ctx", default=None)

def get_trace_context() -> Optional[TraceContext]:
    return _trace_ctx.get()

def start_trace_capture() -> TraceContext:
    ctx = TraceContext()
    _trace_ctx.set(ctx)
    return ctx

def record_model_call(usage_info: Dict[str, Any], model: str):
    ctx = get_trace_context()
    if ctx and ctx.current_event:
        ctx.current_event.model_calls.append({
            "model": model,
            "tokens_in": usage_info.get("input_tokens", 0),
            "tokens_out": usage_info.get("output_tokens", 0),
            "latency_ms": int(usage_info.get("elapsed_s", 0) * 1000),
        })

# ── 装饰器 ───────────────────────────────────────────────────────────────────

def trace(name: str | None = None) -> Callable[[F], F]:
    """
    装饰器：打印日志并采集结构化 Trace。
    """
    def decorator(func: F) -> F:
        label = name or func.__qualname__
        stage_name = label.lower().replace(".", "_")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t_start = time.time()
            ctx = get_trace_context()
            event = None
            
            if ctx:
                event = TraceEvent(
                    stage=stage_name,
                    started_at=datetime.now().isoformat(),
                )
                prev_event = ctx.current_event
                ctx.current_event = event
                ctx.add_event(event)

            print(f"[TRACE] ▶ {label} 开始")
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - t_start
                print(f"[TRACE] ✓ {label} 完成 | 耗时: {elapsed:.2f}s")
                
                if event:
                    event.duration_ms = elapsed * 1000
                    event.status = "done"
                    # 这里暂时不存全量 I/O 以免内存爆炸，Runner 会从 State 中取
                
                return result
            except Exception as e:
                elapsed = time.time() - t_start
                print(f"[TRACE] ✗ {label} 失败 | 耗时: {elapsed:.2f}s | 错误: {e}")
                
                if event:
                    event.duration_ms = elapsed * 1000
                    event.status = "error"
                    event.error = str(e)
                raise
            finally:
                if ctx:
                    ctx.current_event = prev_event if 'prev_event' in locals() else None

        # 支持 async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                t_start = time.time()
                ctx = get_trace_context()
                event = None
                
                if ctx:
                    event = TraceEvent(
                        stage=stage_name,
                        started_at=datetime.now().isoformat(),
                    )
                    prev_event = ctx.current_event
                    ctx.current_event = event
                    ctx.add_event(event)

                print(f"[TRACE] ▶ {label} 开始")
                try:
                    result = await func(*args, **kwargs)
                    elapsed = time.time() - t_start
                    print(f"[TRACE] ✓ {label} 完成 | 耗时: {elapsed:.2f}s")
                    
                    if event:
                        event.duration_ms = elapsed * 1000
                        event.status = "done"
                    
                    return result
                except Exception as e:
                    elapsed = time.time() - t_start
                    print(f"[TRACE] ✗ {label} 失败 | 耗时: {elapsed:.2f}s | 错误: {e}")
                    
                    if event:
                        event.duration_ms = elapsed * 1000
                        event.status = "error"
                        event.error = str(e)
                    raise
                finally:
                    if ctx:
                        ctx.current_event = prev_event if 'prev_event' in locals() else None
            return async_wrapper  # type: ignore
        return wrapper  # type: ignore

    return decorator
