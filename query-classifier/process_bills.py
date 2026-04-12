#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import re
import csv
import io
import subprocess
from typing import List, Tuple, Optional, Dict, Any

# =========================
# 配置区
# =========================
INPUT_FILE = "任务ID_79085004.txt"
OUTPUT_FILE = "query_scores_final.txt"
PROGRESS_FILE = "process_progress.json"
FAILED_LOG = "failed_batches.jsonl"

# 批处理
BATCH_SIZE = 50               # 建议 30~50；100 更容易截断
MIN_BATCH_SIZE = 8            # 解析失败时自动拆批，最小拆到这个
MAX_RETRIES = 4               # 每个 batch（或拆分后的子 batch）最多重试次数
CALL_TIMEOUT = 90             # 单次 CLI 调用超时

# 频率控制
MIN_INTERVAL = 1.2            # 两次成功调用之间的最小间隔（秒）
WAIT_STATUS_EVERY = 30        # 等待期间每隔 30s 汇报

# 处理上限（可不改）
TOTAL_ROWS_LIMIT = 21997

# 你希望优先尝试的模型（会自动探测）
PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-lite-preview",
    "gemini-2.5-flash-preview",
]

SYSTEM_PROMPT = """你是一个文本意图深度分析专家。请根据 Query 对以下两个维度进行打分（0-10分）：

维度 1：多商品意图 (Multi-Product)
- 定义：用户是否在挑选、对比或咨询多个实物商品的购买建议。
- 评分参考：10分表示明确要求对比多款商品或询问购买策略（如“冰箱怎么选”）；0分表示完全不涉及实物商品购买。

维度 2：多机构/服务意图 (Multi-Institution)
- 定义：用户是否意图寻找多个服务提供商、机构、平台或线下网点作为备选。
- 评分参考：
    * 10分：明确要求推荐多个机构、对比服务商、寻找附近网点（如“附近的婚宴酒店”、“上门疏通”）。
    * 7-8分：隐含的服务需求（如“劳动法咨询”、“学车”），用户天然需要寻找专业的服务方。
    * 0分：二选一（A好还是B好）、寻找单一最强、仅查询知识/价格、搜索特定品牌名（如“英孚培训”）。

## 输出要求
1. 必须严格按照输入顺序输出。
2. 输出必须是一个 JSON 数组（不要 markdown，不要解释），长度与输入条数一致。
3. 数组每个元素是二元数组：[维度1分数, 维度2分数]（分数为 0-10 的整数）。
示例：
[[2, 9], [10, 0]]
"""

# =========================
# 工具函数
# =========================

def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def atomic_write_json(path: str, obj: Any):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)

def load_progress() -> int:
    if not os.path.exists(PROGRESS_FILE):
        return 0
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("last_index", 0))
    except Exception as e:
        print(f"[{now_str()}] ⚠️ 进度文件损坏或不可读，将从 0 开始。err={e}")
        return 0

def save_progress(idx: int):
    atomic_write_json(PROGRESS_FILE, {"last_index": idx, "updated_at": now_str()})

def append_failed_log(record: Dict[str, Any]):
    record["ts"] = now_str()
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def get_all_queries(file_path: str) -> List[str]:
    encodings = ["utf-8", "gb18030", "utf-16"]
    for enc in encodings:
        try:
            queries = []
            with open(file_path, mode="r", encoding=enc) as f:
                content = f.read()
                if not content:
                    continue
                reader = csv.reader(io.StringIO(content), delimiter="\t")
                # 跳过 header（如果有）
                try:
                    _ = next(reader)
                except Exception:
                    pass
                for row in reader:
                    if row:
                        q = row[0].strip()
                        if q:
                            queries.append(q)
                print(f"成功使用 {enc} 编码读取到 {len(queries)} 条数据。")
                return queries
        except Exception:
            continue
    raise RuntimeError("文件读取失败：尝试 utf-8 / gb18030 / utf-16 均失败")

def build_prompt(chunk: List[str]) -> str:
    formatted = "\n".join([f"{i+1}. {q}" for i, q in enumerate(chunk)])
    return f"{SYSTEM_PROMPT}\n\n待分析 Query 列表：\n{formatted}"

# -------------------------
# CLI 调用与错误分类
# -------------------------

def run_cli(prompt: str, model: Optional[str], timeout: int) -> Tuple[int, str, str]:
    cmd = ["gemini"]
    if model:
        cmd += ["-m", model]
    cmd += ["-p", prompt, "--output-format", "json"]

    try:
        r = subprocess.run(
            cmd,
            input="",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT: gemini CLI hung >{timeout}s"
    except Exception as e:
        return 1, "", f"EXCEPTION: {e}"

def extract_cli_response(stdout: str) -> Optional[str]:
    """
    gemini --output-format json 的 stdout 是 wrapper JSON:
    { session_id, response, stats ... }
    """
    if not stdout:
        return None
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict):
            if isinstance(obj.get("response"), str):
                return obj["response"]
            for k in ["text", "message", "output"]:
                if isinstance(obj.get(k), str):
                    return obj[k]
    except Exception:
        return None
    return None

def parse_retry_delay_seconds(text: str) -> Optional[float]:
    """
    从 stderr / report 文本中解析 retryDelay / Retry-After
    """
    if not text:
        return None
    patterns = [
        r'"retryDelay"\s*:\s*"(\d+)s"',
        r"retryDelay[^0-9]*([0-9]+)\s*s",
        r"Retry-After[^0-9]*([0-9]+)",
        r"Please retry in\s*([0-9]+(?:\.[0-9]+)?)s",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None

def classify_cli_error(rc: int, stdout: str, stderr: str, model_text: Optional[str]) -> Tuple[str, str]:
    """
    返回 (error_type, reason)
    error_type:
      OK / RATE_LIMIT / DAILY_QUOTA / MODEL_NOT_FOUND / AUTH / NETWORK / TIMEOUT / OTHER
    """
    if rc == 0 and stdout.strip():
        # 进一步看 wrapper 是否可解出 response
        if model_text is None:
            return "OTHER", "wrapper_json_no_response"
        return "OK", "ok"

    s = (stderr or "") + "\n" + (stdout or "")
    s_low = s.lower()

    if rc == 124 or "timeout" in s_low:
        return "TIMEOUT", "timeout"

    # Model not found
    if "modelnotfound" in s_low or "requested entity was not found" in s_low or "not found" in s_low and "model" in s_low:
        return "MODEL_NOT_FOUND", "model_not_found"

    # Auth
    if "login required" in s_low or "authentication" in s_low and "succeeded" not in s_low:
        return "AUTH", "auth_required_or_failed"

    # Rate limit / quota
    if "resource_exhausted" in s_low or "429" in s_low or "rate limit" in s_low or "quota exceeded" in s_low:
        # 区分 daily quota
        if "per day" in s_low or "requests/day" in s_low or "daily" in s_low:
            return "DAILY_QUOTA", "daily_quota_exhausted"
        return "RATE_LIMIT", "rate_limited"

    # 网络/代理/SSL
    if "eof occurred in violation of protocol" in s_low or "ssl" in s_low or "proxy" in s_low or "connection refused" in s_low:
        return "NETWORK", "network_or_proxy_or_ssl"

    return "OTHER", "other_error"

# -------------------------
# 解析 scores：强鲁棒
# -------------------------

def strip_markdown_codeblock(text: str) -> str:
    if not text:
        return ""
    t = text.strip()

    # 提取 ```json ... ``` 里面的内容
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return t

def parse_json_scores(model_text: str) -> Optional[List[Tuple[int, int]]]:
    """
    解析模型输出（应为 JSON array: [[a,b],[a,b],...])
    允许：
    - 外层带 ```json``` code block
    - 混杂少量前后文字（会截取最外层 []）
    """
    if not model_text:
        return None
    t = strip_markdown_codeblock(model_text)

    # 先尝试直接 loads
    obj = None
    try:
        obj = json.loads(t)
    except Exception:
        # 尝试截取最外层 [...]
        start = t.find("[")
        end = t.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(t[start:end+1])
            except Exception:
                return None
        else:
            return None

    if not isinstance(obj, list):
        return None

    out: List[Tuple[int, int]] = []
    for x in obj:
        if isinstance(x, list) and len(x) >= 2:
            try:
                a = int(float(x[0]))
                b = int(float(x[1]))
                a = max(0, min(10, a))
                b = max(0, min(10, b))
                out.append((a, b))
            except Exception:
                continue

    return out if out else None

# -------------------------
# 等待时每 30s 报告
# -------------------------

def sleep_with_status(total_seconds: float, reason: str):
    remaining = int(max(0, total_seconds))
    start = time.time()
    while remaining > 0:
        step = min(WAIT_STATUS_EVERY, remaining)
        print(f"[{now_str()}] ⏳ 等待中：{reason} | 剩余 {remaining}s ...")
        time.sleep(step)
        elapsed = int(time.time() - start)
        remaining = int(max(0, total_seconds - elapsed))

# -------------------------
# Batch 调用：模型轮换 + 冷却
# -------------------------

def call_once(prompt: str, model: str) -> Tuple[str, str, int, str, str]:
    """
    返回 (error_type, reason, rc, stdout, stderr)
    """
    rc, stdout, stderr = run_cli(prompt, model=model, timeout=CALL_TIMEOUT)
    model_text = extract_cli_response(stdout)
    et, reason = classify_cli_error(rc, stdout, stderr, model_text)
    return et, reason, rc, stdout, stderr

def call_with_models(prompt: str, models: List[str]) -> Tuple[Optional[str], Optional[str], str, str, str]:
    """
    轮换模型调用，返回：
      model_text, used_model, status, stdout, stderr
    status:
      OK / RATE_LIMIT / DAILY_QUOTA / MODEL_NOT_FOUND / AUTH / NETWORK / TIMEOUT / OTHER
    """
    cooldown_until: Dict[str, float] = {m: 0.0 for m in models}
    last_stdout = ""
    last_stderr = ""
    last_status = "OTHER"
    last_model = None

    for attempt in range(1, MAX_RETRIES + 1):
        rate_waits = []
        any_tried = False

        for m in models:
            if time.time() < cooldown_until.get(m, 0.0):
                continue
            any_tried = True

            et, reason, rc, stdout, stderr = call_once(prompt, m)
            last_stdout, last_stderr, last_status, last_model = stdout, stderr, et, m

            if et == "OK":
                model_text = extract_cli_response(stdout)
                return model_text, m, "OK", stdout, stderr

            if et == "DAILY_QUOTA":
                # 直接终止
                print(f"[{now_str()}] 🛑 日配额耗尽：{m} | {reason}")
                return None, m, "DAILY_QUOTA", stdout, stderr

            if et == "MODEL_NOT_FOUND":
                print(f"[{now_str()}] ⚠️ 模型不可用：{m} | {reason}")
                continue

            if et == "RATE_LIMIT":
                wait_s = parse_retry_delay_seconds(stderr) or parse_retry_delay_seconds(stdout) or min(60, 2 ** attempt)
                # 给模型冷却
                cooldown_until[m] = time.time() + float(wait_s)
                rate_waits.append(float(wait_s))
                print(f"[{now_str()}] ⚠️ 限流：{m} | 建议等待 {wait_s:.0f}s 后再试（本轮继续尝试其他模型）")
                continue

            # 其它错误：尝试下一个模型
            print(f"[{now_str()}] ⚠️ 调用失败：{m} | et={et} reason={reason} rc={rc}")
            # 给短冷却，避免立即重复错误
            cooldown_until[m] = time.time() + min(10, attempt * 2)
            continue

        # 本轮如果没有任何模型可试（都在 cooldown），等到最近 cooldown 到期
        if not any_tried:
            soonest = min(cooldown_until.values()) if cooldown_until else (time.time() + 5)
            wait_s = max(1, int(soonest - time.time()))
            sleep_with_status(wait_s, reason="模型冷却中")
            continue

        # 本轮所有可用模型都 rate limited
        if rate_waits:
            wait_s = max(1, int(min(rate_waits)))
            sleep_with_status(wait_s, reason="本轮所有模型均限流")
            continue

        # 其它失败：退避
        wait_s = min(10, attempt * 2)
        sleep_with_status(wait_s, reason="本轮所有模型均失败，退避重试")

    return None, last_model, f"FAILED_{last_status}", last_stdout, last_stderr

# -------------------------
# 自动拆批：解析失败/不匹配时拆分
# -------------------------

def process_chunk(chunk: List[str], models: List[str], base_index: int) -> Tuple[List[Tuple[str,int,int]], int]:
    """
    返回：
      results: [(query, s1, s2), ...]  (可能为空)
      processed_count: 本次实际“推进”的 query 数（成功或失败都推进）
    """
    prompt = build_prompt(chunk)
    model_text, used_model, status, stdout, stderr = call_with_models(prompt, models=models)

    # 日配额：直接抛出让 main 终止
    if status == "DAILY_QUOTA":
        raise RuntimeError("DAILY_QUOTA")

    if model_text is None:
        # 调用失败：记录并跳过推进
        print(f"[{now_str()}] ❌ 本批调用失败：status={status} model={used_model}")
        append_failed_log({
            "type": "CALL_FAILED",
            "index": base_index,
            "size": len(chunk),
            "model": used_model,
            "status": status,
            "stderr_preview": (stderr or "")[:1200],
            "stdout_preview": (stdout or "")[:1200],
            "queries_preview": chunk[:5],
        })
        return [], len(chunk)

    scores = parse_json_scores(model_text)
    if scores is not None and len(scores) == len(chunk):
        # 成功
        rows = [(q, s1, s2) for q, (s1, s2) in zip(chunk, scores)]
        return rows, len(chunk)

    # 解析失败或数量不匹配：尝试拆分
    got = 0 if scores is None else len(scores)
    print(f"[{now_str()}] ⚠️ 解析失败/数量不匹配：需 {len(chunk)}，实得 {got} | model={used_model}")
    append_failed_log({
        "type": "PARSE_MISMATCH",
        "index": base_index,
        "size": len(chunk),
        "model": used_model,
        "need": len(chunk),
        "got": got,
        "stderr_preview": (stderr or "")[:1200],
        "model_text_preview": (model_text or "")[:1200],
        "queries_preview": chunk[:5],
    })

    # 如果已经很小了，就放弃这批（推进进度，避免卡死）
    if len(chunk) <= MIN_BATCH_SIZE:
        print(f"[{now_str()}] 🟡 已到最小批次 {MIN_BATCH_SIZE}，仍失败：记录后跳过推进。")
        return [], len(chunk)

    # 拆成两半递归处理
    mid = len(chunk) // 2
    left = chunk[:mid]
    right = chunk[mid:]

    left_rows, left_cnt = process_chunk(left, models, base_index)
    right_rows, right_cnt = process_chunk(right, models, base_index + mid)

    return left_rows + right_rows, left_cnt + right_cnt

# -------------------------
# 探测可用模型
# -------------------------

def probe_models(models: List[str]) -> List[str]:
    ok = []
    print("🔎 启动前探测可用模型...")
    for m in models:
        et, reason, rc, stdout, stderr = call_once("ping", m)
        if et == "OK":
            print(f"  ✅ {m}")
            ok.append(m)
        else:
            print(f"  ❌ {m}  (et={et} reason={reason})")
    return ok

# =========================
# 主流程
# =========================

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"找不到文件 {INPUT_FILE}")
        return

    all_queries = get_all_queries(INPUT_FILE)
    total_count = min(len(all_queries), TOTAL_ROWS_LIMIT)

    # 输出文件表头
    last_index = load_progress()
    if last_index == 0 and not os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
            f.write("query\tmulti_product_score\tmulti_institution_score\n")

    # 探测模型池
    usable_models = probe_models(PREFERRED_MODELS)
    if not usable_models:
        print("🛑 没有任何可用模型。请先在终端运行 gemini 并 /auth 登录，然后重试。")
        return

    print(f"\n🚀 任务启动 [Gemini CLI OAuth]: 共 {total_count} 条，从第 {last_index + 1} 条开始...")
    print(f"🧠 可用模型池：{', '.join(usable_models)}")
    print(f"📦 batch_size={BATCH_SIZE} | min_interval={MIN_INTERVAL}s | max_retries={MAX_RETRIES} | timeout={CALL_TIMEOUT}s\n")

    last_call_ok_ts = 0.0

    i = last_index
    while i < total_count:
        chunk = all_queries[i: i + BATCH_SIZE]
        pct = (i / total_count) * 100.0
        print(f"[进度] {i}/{total_count} ({pct:.2f}%) - 处理 {len(chunk)} 条...")

        # 成功调用之间的最小间隔（避免撞限流）
        since_ok = time.time() - last_call_ok_ts if last_call_ok_ts > 0 else None
        if since_ok is not None and since_ok < MIN_INTERVAL:
            sleep_s = MIN_INTERVAL - since_ok
            sleep_with_status(sleep_s, reason="min_interval")

        try:
            rows, processed_cnt = process_chunk(chunk, usable_models, base_index=i)
        except RuntimeError as e:
            if str(e) == "DAILY_QUOTA":
                print(f"[{now_str()}] 🛑 检测到按天配额耗尽，脚本停止。明天（PT 午夜后）或提升配额再继续。")
                return
            raise

        # 写入成功结果
        if rows:
            with open(OUTPUT_FILE, "a", encoding="utf-8-sig", newline="") as f:
                for q, s1, s2 in rows:
                    f.write(f"{q}\t{s1}\t{s2}\n")
            last_call_ok_ts = time.time()
            print(f"[{now_str()}] ✅ 写入 {len(rows)} 条结果。")

        # 推进进度（无论成功与否都推进，避免卡死）
        i += processed_cnt
        save_progress(i)

    print(f"\n[{now_str()}] 🎉 全部完成！输出：{OUTPUT_FILE} | 失败日志：{FAILED_LOG} | 进度：{PROGRESS_FILE}")

if __name__ == "__main__":
    main()