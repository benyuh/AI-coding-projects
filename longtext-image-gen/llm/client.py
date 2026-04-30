"""
llm/client.py — 统一 LLM 客户端（v3.1.2）

功能：
- 统一封装 Anthropic API 调用
- 自动重试（指数退避，最多 3 次）
- JSON 防御解析（同 v3.1.1 的 _parse_json_robust）
- token 统计打印
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional, Type

import anthropic

from infra.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    LLM_PRIMARY_MODEL,
    LLM_FAST_MODEL,
)


# ── 全局客户端（延迟初始化）─────────────────────────────────────────────────

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            base_url=ANTHROPIC_BASE_URL,
        )
    return _client


# ── JSON 清理（复用 v3.1.1 经验证的策略）────────────────────────────────────

def _clean_json_string_newlines(s: str) -> str:
    """处理 LLM 字符串值内的未转义换行/控制字符/嵌套引号。"""
    s = s.replace('\u201c', '「').replace('\u201d', '」')
    s = s.replace('\u2018', "'").replace('\u2019', "'")

    result = []
    in_string = False
    escape_next = False
    chars = list(s)

    for i, char in enumerate(chars):
        if escape_next:
            result.append(char)
            escape_next = False
            continue
        if char == '\\' and in_string:
            result.append(char)
            escape_next = True
            continue
        if char == '"':
            if not in_string:
                in_string = True
                result.append(char)
            else:
                next_non_ws = ''
                for j in range(i + 1, min(i + 10, len(chars))):
                    c = chars[j]
                    if c not in ' \t\r\n':
                        next_non_ws = c
                        break
                if next_non_ws in (':', ',', ']', '}', ''):
                    in_string = False
                    result.append(char)
                else:
                    result.append('\\"')
            continue
        if in_string and char in '\n\r\t':
            result.append({'\\n': '\\n', '\\r': '\\r', '\\t': '\\t'}.get(char, '\\n'))
            # 直接用替换字典
            replacements = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
            del result[-1]
            result.append(replacements[char])
            continue
        result.append(char)
    return ''.join(result)


def _try_truncate_recovery(raw: str) -> Optional[dict]:
    """截断恢复：从末尾回退找最后完整 }，补 ]} 尝试解析。"""
    clean = _clean_json_string_newlines(raw)
    last_brace = clean.rfind('}')
    if last_brace == -1:
        return None
    for suffix in [']}', '\n  ]}', '\n]}']:
        try:
            return json.loads(clean[:last_brace + 1] + suffix)
        except json.JSONDecodeError:
            pass
    pos = last_brace
    for _ in range(5):
        pos = clean.rfind('}', 0, pos)
        if pos == -1:
            break
        for suffix in [']}', '\n  ]}', '\n]}']:
            try:
                return json.loads(clean[:pos + 1] + suffix)
            except json.JSONDecodeError:
                pass
    return None


def parse_json_robust(raw: str) -> dict:
    """4 级鲁棒 JSON 解析，抛出 json.JSONDecodeError 如果全部失败。"""
    # 去除 Markdown 代码块包裹
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = []
        for line in lines[1:]:
            if line.strip() == "```":
                break
            inner.append(line)
        raw = "\n".join(inner)

    # 级别 1
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 级别 2
    try:
        cleaned = _clean_json_string_newlines(raw)
        result = json.loads(cleaned)
        print("[LLM] JSON 清理后解析成功")
        return result
    except Exception:
        pass

    # 级别 3
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        for attempt in [match.group(0), _clean_json_string_newlines(match.group(0))]:
            try:
                result = json.loads(attempt)
                print("[LLM] 提取 JSON 块解析成功")
                return result
            except Exception:
                pass

    # 级别 4
    result = _try_truncate_recovery(raw)
    if result:
        print("[LLM] 截断恢复解析成功")
        return result

    raise json.JSONDecodeError("所有解析策略均失败", raw, 0)


# ── 核心调用函数 ─────────────────────────────────────────────────────────────

def call_llm(
    user_prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    max_tokens: int = 8192,
    expect_json: bool = True,
    label: str = "LLM",
    max_retries: int = 3,
) -> tuple[dict | str, dict]:
    """
    统一 LLM 调用接口。

    Returns:
        (parsed_result, usage_info)
        - parsed_result: expect_json=True 时为 dict，否则为 str
        - usage_info: {"input_tokens": int, "output_tokens": int, "elapsed_s": float}
    """
    client = _get_client()
    _model = model or LLM_PRIMARY_MODEL

    last_error: Exception = RuntimeError("未执行任何调用")

    for attempt in range(1, max_retries + 1):
        t_start = time.time()
        try:
            message = client.messages.create(
                model=_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            elapsed = time.time() - t_start

            usage = getattr(message, "usage", None)
            usage_info = {
                "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
                "elapsed_s": elapsed,
            }
            # 记录 Trace
            from infra.tracing import record_model_call
            record_model_call(usage_info, model=_model)

            print(
                f"[{label}] 耗时: {elapsed:.1f}s | "
                f"input: {usage_info['input_tokens']} | "
                f"output: {usage_info['output_tokens']} tokens"
            )

            raw = message.content[0].text.strip()

            if message.stop_reason == "max_tokens":
                print(f"[{label}] WARN: max_tokens 截断，尝试修复")

            if expect_json:
                result = parse_json_robust(raw)
            else:
                result = raw

            return result, usage_info

        except (anthropic.InternalServerError, anthropic.RateLimitError,
                anthropic.APIConnectionError) as e:
            elapsed = time.time() - t_start
            print(f"[{label}] 调用失败 (尝试 {attempt}/{max_retries}): {e}")
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"[{label}] {wait}s 后重试...")
                time.sleep(wait)
            continue

        except json.JSONDecodeError as e:
            print(f"[{label}] JSON 解析失败 (尝试 {attempt}/{max_retries}): {e}")
            last_error = e
            if attempt < max_retries:
                time.sleep(1)
            continue

    raise RuntimeError(f"[{label}] 连续 {max_retries} 次失败: {last_error}")
