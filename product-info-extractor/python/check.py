import google.generativeai as genai
import os

# 粘贴你的 API Key
API_KEY = "YOUR_GEMINI_API_KEY_HERE" 

genai.configure(api_key=API_KEY.strip())

print("正在查询可用模型...")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"- {m.name}")
except Exception as e:
    print(f"出错啦: {e}")