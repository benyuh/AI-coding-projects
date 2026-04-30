"""
service/job_store.py — 线程安全的内存任务状态存储（v3.1.5）

生产环境应替换为 Redis / PostgreSQL，此处用 dict + threading.Lock 实现。
"""
from __future__ import annotations

import threading
import uuid
from typing import Dict, Optional

from service.schemas import JobResult, JobStatus


class InMemoryJobStore:
    """线程安全的内存任务存储，支持创建/更新/查询。"""

    def __init__(self) -> None:
        self._store: Dict[str, JobResult] = {}
        self._lock = threading.Lock()

    def create(self, job_id: Optional[str] = None) -> str:
        """创建新任务，返回 job_id。"""
        jid = job_id or str(uuid.uuid4())
        with self._lock:
            self._store[jid] = JobResult(job_id=jid, status=JobStatus.PENDING)
        return jid

    def update(self, job_id: str, **kwargs) -> None:
        """更新任务字段（线程安全）。"""
        with self._lock:
            if job_id not in self._store:
                return
            current = self._store[job_id]
            self._store[job_id] = current.model_copy(update=kwargs)

    def get(self, job_id: str) -> Optional[JobResult]:
        with self._lock:
            return self._store.get(job_id)

    def exists(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._store


# 全局单例（FastAPI lifespan 会初始化）
_job_store: Optional[InMemoryJobStore] = None


def get_job_store() -> InMemoryJobStore:
    """FastAPI 依赖注入 / 直接调用均可。"""
    global _job_store
    if _job_store is None:
        _job_store = InMemoryJobStore()
    return _job_store
