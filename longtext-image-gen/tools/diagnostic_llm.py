"""一次性诊断脚本：验证 LLM 真实可用 + token 计数正常。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from llm.client import call_llm

result, usage = call_llm(
    user_prompt="请用一句话回答：1+1等于几？返回 JSON: {\"answer\": \"...\"}",
    expect_json=True,
    label="DiagPing",
)
print(f"\n[诊断] 解析结果: {result}")
print(f"[诊断] input_tokens={usage['input_tokens']} output_tokens={usage['output_tokens']} elapsed={usage['elapsed_s']:.2f}s")
assert usage['input_tokens'] > 0, "input_tokens=0，LLM 调用未生效，检查 .env / oneAPI 配额"
assert usage['output_tokens'] > 0, "output_tokens=0，LLM 调用未生效"
print("[诊断] ✅ LLM 调用正常")
