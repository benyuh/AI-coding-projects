"""
infra/config.py — 环境配置读取（v3.1.2）
"""

import os
import pathlib

_HERE = pathlib.Path(__file__).parent.parent  # 项目根目录

# 从 .env 加载
_env_file = _HERE / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL: str = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
LLM_PRIMARY_MODEL: str = os.environ.get("LLM_PRIMARY_MODEL", "claude-sonnet-4-6")
LLM_FAST_MODEL: str = os.environ.get("LLM_FAST_MODEL", "claude-haiku-4-5-20251001")

# 并发控制
WORKER_A_MAX_CONCURRENCY: int = int(os.environ.get("WORKER_A_MAX_CONCURRENCY", "8"))

# 演示保底模式（LONGTEXT_DEMO_SAFE_MODE=1 开启）
# 提高重试预算、增加卡片重试次数，降低过早降级风险，确保演示成功率
DEMO_SAFE_MODE: bool = os.environ.get("LONGTEXT_DEMO_SAFE_MODE", "0").strip() == "1"

# demo_safe_mode 下的重试预算上限（vs 正常模式）
# 正常：render_only=3, blueprint_level=2, fact_drift=1
# demo:  render_only=6, blueprint_level=4, fact_drift=3
DEMO_RETRY_LIMITS: dict = {
    "render_only": int(os.environ.get("DEMO_RETRY_RENDER_ONLY", "6")),
    "blueprint_level": int(os.environ.get("DEMO_RETRY_BLUEPRINT_LEVEL", "4")),
    "fact_drift": int(os.environ.get("DEMO_RETRY_FACT_DRIFT", "3")),
}

# demo_safe_mode 下每张卡片 A→B 最大重试次数（正常=2，demo=3）
DEMO_MAX_CARD_RETRIES: int = int(os.environ.get("DEMO_MAX_CARD_RETRIES", "3"))
