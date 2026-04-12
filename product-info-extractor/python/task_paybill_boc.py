import os
import json
import time
import glob
import io  # 新增引用
from datetime import datetime
from google import genai
from google.genai import types

# ================= 配置区域 =================

# 1. 你的 API Key
API_KEY = "YOUR_GEMINI_API_KEY_HERE"

# 2. 任务名称
TASK_NAME = "boc_credit_card_bills"

# 3. 针对【信用卡账单】的提取 Prompt
PROMPT_TEXT = """
你是一个专业的财务数据提取助手。请分析这份信用卡账单 PDF，提取具体的交易明细表格。

**提取范围定位：**
请重点寻找在文本 “注：若您名下的多张信用卡主卡均有欠款，需分别还款。” 之后，且在 “用卡安全温馨提示：” 之前的内容。
通常这部分是一个表格。

**提取字段要求 (请返回 JSON 列表)：**
1. transaction_date: 交易日期 (格式: YYYY-MM-DD)
2. posting_date: 入账日期 (格式: YYYY-MM-DD)
3. description: 交易摘要 (商户名称或描述)
4. transaction_amount: 交易金额 (数字，保留正负号)
5. transaction_currency: 交易货币
6. posting_amount: 入账金额 (数字)
7. posting_currency: 入账货币

**格式要求：**
- 请直接返回纯 JSON 格式数据（一个包含多个交易对象的列表）。
- 不要使用 Markdown 标记（如 ```json）。
- 如果没有找到任何交易明细，请返回空列表 []。
"""

# 4. 模型分级池
# 优先级最高：高端模型
HIGH_END_MODELS = [
    "gemini-2.0-flash",       
    "gemini-1.5-pro",         
] 

# 备用：低端模型
LOW_END_MODELS = [
    "gemini-1.5-flash", 
    "gemini-2.0-flash-lite-preview"
]

# ================= 状态管理逻辑 =================

STATUS_FILE_NAME = "daily_model_status.json"
low_end_model_index = 0 

def get_status_file_path(project_root):
    return os.path.join(project_root, "results", STATUS_FILE_NAME)

def load_daily_status(file_path):
    """读取当天的错误统计状态"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    default_status = {"date": today_str, "high_end_errors": 0, "total_errors": 0}
    
    if not os.path.exists(file_path):
        return default_status
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            status = json.load(f)
            if status.get("date") != today_str:
                return default_status
            return status
    except:
        return default_status

def record_error(file_path, model_name):
    """记录一次错误"""
    status = load_daily_status(file_path)
    status["total_errors"] += 1
    
    if model_name in HIGH_END_MODELS:
        status["high_end_errors"] += 1
        
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    
    return status

def get_best_model(status_file_path):
    """根据当天状态决定使用哪个模型"""
    global low_end_model_index
    
    status = load_daily_status(status_file_path)
    
    # 熔断规则
    if status["total_errors"] >= 10:
        return None, "STOP_LIMIT_REACHED"
    
    # 降级规则
    if status["high_end_errors"] >= 1:
        model = LOW_END_MODELS[low_end_model_index % len(LOW_END_MODELS)]
        return model, "LOW_END"
    
    # 默认高端
    return HIGH_END_MODELS[0], "HIGH_END"

# ================= 主程序 =================

def save_results(results, filepath):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存文件失败: {e}")

def main():
    # 初始化新版 Client
    client = genai.Client(api_key=API_KEY.strip())
    
    # 路径设置
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    pdf_folder = os.path.join(project_root, "images", "pdf_bills_boc") 
    output_folder = os.path.join(project_root, "results")
    output_file = os.path.join(output_folder, f"{TASK_NAME}_data.json")
    status_file = get_status_file_path(project_root)

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    if not os.path.exists(pdf_folder):
        print(f"❌ 找不到 PDF 文件夹: {pdf_folder}")
        return

    # 断点续传
    results = []
    processed_files = set()
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if content:
                    results = json.loads(content)
                    for item in results:
                        if 'file_name' in item:
                            processed_files.add(item['file_name'])
            print(f"📖 发现已有数据，已跳过 {len(processed_files)} 个处理过的文件。")
        except Exception:
            pass

    # 获取文件
    all_files = glob.glob(os.path.join(pdf_folder, "*.pdf"))
    files_to_process = [f for f in all_files if os.path.basename(f) not in processed_files]
    files_to_process.sort()
    total_files = len(files_to_process)

    if total_files == 0:
        print("🎉 所有 PDF 都已处理完成！")
        return

    # 初始状态检查
    current_status = load_daily_status(status_file)
    print(f"🚀 任务启动 (编码修复版) | 当天累计错误: {current_status['total_errors']} | 高端模型错误: {current_status['high_end_errors']}")
    if current_status['total_errors'] >= 10:
        print("🛑 当天累计报错已达 10 次 (请手动删除 results/daily_model_status.json 以重置)。")
        return

    # 循环处理
    for i, file_path in enumerate(files_to_process):
        filename = os.path.basename(file_path)
        success = False
        uploaded_file = None 

        while not success:
            current_model_name, strategy_type = get_best_model(status_file)
            
            if strategy_type == "STOP_LIMIT_REACHED":
                print(f"\n🛑 检测到当天累计报错已达 10 次，正在停止任务...")
                return 

            print(f"[{i+1}/{total_files}] 处理: {filename} (模型: {current_model_name} | 策略: {strategy_type})...", end="", flush=True)
            
            try:
                # 步骤 A: 上传 (核心修复：使用 BytesIO 绕过文件名编码问题)
                if not uploaded_file:
                    with open(file_path, 'rb') as f:
                        file_content = f.read()
                    
                    # 创建内存文件流
                    file_stream = io.BytesIO(file_content)
                    
                    # 上传流，并强制指定一个安全的英文名和 MIME 类型
                    # 这样 API 收到的 headers 里全是英文，就不会报错了
                    uploaded_file = client.files.upload(
                        file=file_stream,
                        config=types.UploadFileConfig(
                            display_name="bill_temp.pdf", 
                            mime_type="application/pdf"
                        )
                    )
                    
                    # 等待处理完成
                    while uploaded_file.state.name == "PROCESSING":
                        time.sleep(1)
                        uploaded_file = client.files.get(name=uploaded_file.name)
                    
                    if uploaded_file.state.name == "FAILED":
                        raise ValueError("PDF 上传失败 (服务端处理错误)")

                # 步骤 B: 生成
                response = client.models.generate_content(
                    model=current_model_name,
                    contents=[uploaded_file, PROMPT_TEXT],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                # 步骤 C: 解析
                try:
                    clean_text = response.text.strip()
                    if clean_text.startswith("```json"):
                        clean_text = clean_text.replace('```json', '').replace('```', '')
                    
                    transactions = json.loads(clean_text)
                    
                    file_result = {
                        "file_name": filename, # 这里依然保留你的原始中文文件名
                        "transactions": transactions, 
                        "transaction_count": len(transactions) if isinstance(transactions, list) else 0,
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "model_used": current_model_name
                    }
                    
                    results.append(file_result)
                    save_results(results, output_file)
                    
                    print(f" ✅ 成功 ({file_result['transaction_count']} 条)")
                    success = True
                    
                    # 清理
                    try:
                        client.files.delete(name=uploaded_file.name)
                        uploaded_file = None
                    except:
                        pass
                    time.sleep(2)

                except json.JSONDecodeError:
                    print(f" ⚠️ JSON 解析失败")
                    raise ValueError("模型返回的不是有效的 JSON")

            except Exception as e:
                print(f" ❌ 失败")
                error_msg = str(e)
                if "404" in error_msg:
                    print(f"   ↪️ 模型不可用 (404)")
                else:
                    print(f"   ↪️ 错误详情: {error_msg[:100]}...")
                
                # 记录错误
                new_status = record_error(status_file, current_model_name)
                
                if strategy_type == "LOW_END":
                    global low_end_model_index
                    low_end_model_index += 1
                
                if new_status["total_errors"] >= 10:
                    print("   🛑 达到当天最大错误次数限制，任务停止。")
                    return 
                
                time.sleep(3)
                continue

    print(f"\n🎉 全部完成！结果已保存: {output_file}")

if __name__ == "__main__":
    main()