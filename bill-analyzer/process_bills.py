import csv
import subprocess
import io
import time
import os
import json
import re

# --- 配置区 ---
INPUT_FILE = "target_bill_test.txt"      
OUTPUT_FILE = "processed_bills_final.csv"
PROGRESS_FILE = "process_progress.json"  
BATCH_SIZE = 50                          
TOTAL_ROWS_LIMIT = 100                  

SYSTEM_PROMPT = """
你现在是一个纯粹的 CSV 数据转换引擎。
任务：补全数据中缺失的“交易分类_处理后”和“交易方式”。
## 选项范围
1. 交易分类_处理后: [消费, 消费-后退款, 退款, 内部转账（含转账、还款等）, 利息收入]
2. 交易方式: [旅游, 房租, 礼物, 外出吃饭, 食材调料, 家居用品, 体检, 演出, 保险, 份子钱, 回家, 水电煤气网费取暖费, 送人-pp, 送人-父母, 送人-其他人, 理财收入, 在线订阅费, 打车]
## 绝对禁令
- 只输出补全后的原始数据行，严禁任何解释。
"""

def extract_csv_lines(text):
    """提取 CSV 行并过滤废话"""
    lines = text.strip().split('\n')
    valid_lines = []
    for line in lines:
        if re.match(r'^\d+,', line) and line.count(',') >= 5:
            valid_lines.append(line)
    return "\n".join(valid_lines)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f).get("last_index", 0)
        except: return 0
    return 0

def save_progress(index):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({"last_index": index}, f)

def call_gemini_cli_with_status(prompt):
    process = subprocess.Popen(
        ["gemini", "-p", prompt],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8'
    )
    start_time = time.time()
    while True:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            return stdout.strip() if process.returncode == 0 else ""
        time.sleep(10)
        print(f"   [等待] 已耗时 {int(time.time() - start_time)}s...")

def main():
    # 1. 检查文件是否存在
    if not os.path.exists(INPUT_FILE):
        print(f"错误：在当前目录下找不到文件 '{INPUT_FILE}'。")
        print(f"当前目录下的文件有：{os.listdir('.')}")
        return

    # 2. 尝试多种编码方案
    data = []
    header = []
    for enc in ['gb18030', 'utf-16', 'utf-8-sig', 'latin-1']:
        try:
            with open(INPUT_FILE, mode='r', encoding=enc, errors='ignore') as f:
                # 显式读取并解析
                content = f.read()
                if not content: continue
                reader = list(csv.reader(io.StringIO(content), delimiter='\t'))
                if len(reader) > 1:
                    header = reader[0]
                    data = reader[1:TOTAL_ROWS_LIMIT + 1]
                    print(f">>> 成功通过 {enc} 编码读取到 {len(data)} 行数据")
                    break
        except Exception as e:
            continue

    if not data:
        print("错误：读取失败。请确认文件内容不是空的，且确实是制表符分隔。")
        return

    last_index = load_progress()
    if last_index == 0:
        with open(OUTPUT_FILE, 'w', encoding='utf-8-sig') as f:
            f.write(",".join(header) + "\n")

    print(f"任务启动，进度：从第 {last_index + 1} 行开始...")

    for i in range(last_index, len(data), BATCH_SIZE):
        current_chunk = data[i : i + BATCH_SIZE]
        print(f"\n[处理批次] {i + 1} - {i + len(current_chunk)}")
        
        chunk_io = io.StringIO()
        writer = csv.writer(chunk_io)
        writer.writerows(current_chunk)
        
        prompt = f"{SYSTEM_PROMPT}\n\n数据：\n{chunk_io.getvalue()}"
        result = call_gemini_cli_with_status(prompt)
        
        if result:
            clean_result = extract_csv_lines(result)
            if clean_result:
                with open(OUTPUT_FILE, 'a', encoding='utf-8-sig') as f:
                    f.write(clean_result + "\n")
                save_progress(i + BATCH_SIZE)
            else:
                print("   [跳过] AI 返回格式不正确。")
        else:
            print("   [错误] 本批次请求失败。")
            break
        time.sleep(1)

if __name__ == "__main__":
    main()