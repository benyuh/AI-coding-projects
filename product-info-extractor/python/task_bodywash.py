import google.generativeai as genai
import PIL.Image
import os
import json
import time

# ================= 配置区域 =================

# 1. 你的 API Key
API_KEY = "YOUR_GEMINI_API_KEY_HERE"

# 2. 当前品类名称
CATEGORY_NAME = "bodywash"

# 3. 针对【沐浴露】的提取要求
PROMPT_TEXT = """
请分析这张商品详情页图片，提取以下关键信息。
请直接返回纯 JSON 格式数据，不要使用 Markdown 标记。

字段要求：
1. product_name: 商品名称（找字号最大的）。
2. price: 价格（数字）。
3. scent: 香型/味道（关键！如：白茶味、薰衣草、木质调、无香等）。
4. effect: 主打功效（如：持久留香、美白、去鸡皮、清爽控油、滋润保湿）。
5. volume: 净含量/规格（如：500ml, 1L）。

如果某个字段在图中完全找不到，请填 "未找到"。
"""

# 4. 模型接力池 (根据你的账号权限排序)
# 脚本会按顺序使用。如果第一个挂了，自动换第二个，以此类推。
MODEL_POOL = [
    "gemini-2.5-flash",          # 优先用这个 (20次/天)
    "gemini-2.5-flash-lite",     # 备用1 (20次/天)
    "gemini-3-flash-preview",    # 备用2 (20次/天，如果之前没用完的话)
    "gemma-3-27b-it"             # 终极备用 (14000次/天，量大管饱)
]

# ===========================================

current_model_index = 0

def get_current_model_name():
    """获取当前正在使用的模型名称"""
    if current_model_index < len(MODEL_POOL):
        return MODEL_POOL[current_model_index]
    return None

def switch_model():
    """切换到下一个模型"""
    global current_model_index
    current_model_index += 1
    new_model = get_current_model_name()
    if new_model:
        print(f"\n⚠️ 正在切换到下一个模型: {new_model} ...")
        return True
    else:
        print(f"\n❌ 所有模型额度已耗尽！脚本停止。")
        return False

def save_results(results, filepath):
    """实时保存结果到文件"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    # print(f" (已自动保存)") # 调试用，太吵可以注释掉

def main():
    genai.configure(api_key=API_KEY.strip())
    
    # 路径设置
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    image_folder = os.path.join(project_root, "images", CATEGORY_NAME)
    output_folder = os.path.join(project_root, "results")
    output_file = os.path.join(output_folder, f"{CATEGORY_NAME}_data.json")

    # 1. 确保输出目录存在
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 2. 读取已有结果 (断点续传核心)
    results = []
    processed_files = set()
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                results = json.load(f)
                # 记录已经处理过的文件名
                for item in results:
                    if 'file_name' in item:
                        processed_files.add(item['file_name'])
            print(f"📖 发现已有数据，已跳过 {len(processed_files)} 张处理过的图片。")
        except Exception:
            print("⚠️ 读取旧文件失败，将重新开始...")

    # 3. 获取图片列表
    if not os.path.exists(image_folder):
        print(f"❌ 找不到图片文件夹: {image_folder}")
        return

    all_files = [f for f in os.listdir(image_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    # 过滤掉已经处理过的
    files_to_process = [f for f in all_files if f not in processed_files]
    total_files = len(files_to_process)

    if total_files == 0:
        print("🎉 所有图片都已处理完成！无需操作。")
        return

    print(f"🚀 准备处理剩余 {total_files} 张图片，当前模型: {get_current_model_name()}")

    # 4. 开始循环处理
    for i, filename in enumerate(files_to_process):
        img_path = os.path.join(image_folder, filename)
        
        retry_count = 0
        max_retries = 3
        success = False

        while not success:
            current_model_name = get_current_model_name()
            if not current_model_name:
                print("🛑 致命错误：无可用模型，程序退出。")
                return # 彻底结束

            print(f"[{i+1}/{total_files}] 处理: {filename} (模型: {current_model_name})...", end="", flush=True)
            
            try:
                # 初始化模型
                model = genai.GenerativeModel(current_model_name)
                img = PIL.Image.open(img_path)
                
                # 发送请求
                response = model.generate_content([PROMPT_TEXT, img])
                
                # 解析数据
                clean_text = response.text.replace('```json', '').replace('```', '').strip()
                item_data = json.loads(clean_text)
                
                item_data['file_name'] = filename
                item_data['category'] = CATEGORY_NAME
                
                results.append(item_data)
                
                # ✅ 关键：每成功一张，立刻保存！
                save_results(results, output_file)
                
                print(" ✅ 成功")
                success = True
                time.sleep(5) # 成功后休息5秒

            except Exception as e:
                print(f" ❌ 失败")
                error_msg = str(e)
                
                # 判断是不是额度不够了 (429 Resource Exhausted)
                if "429" in error_msg or "Quota exceeded" in error_msg:
                    print(f"   ↪️ 检测到额度不足 ({current_model_name})，直接切换模型...")
                    if not switch_model():
                        return # 没模型了，退出
                    # 切换后重置重试次数，立即重试
                    retry_count = 0 
                    continue 

                # 其他错误 (网络抖动等)，尝试重试
                retry_count += 1
                if retry_count < max_retries:
                    print(f"   ↪️ 出错 ({error_msg})，第 {retry_count} 次重试...")
                    time.sleep(3)
                else:
                    print(f"   🛑 重试多次失败，尝试切换模型...")
                    if not switch_model():
                        print("   ⚠️ 没办法了，跳过这张图，记录错误。")
                        results.append({"file_name": filename, "error": "处理失败: " + error_msg})
                        save_results(results, output_file) # 即使失败也记录并保存
                        success = True # 强制标记为完成以免死循环，处理下一张

    print(f"\n🎉 全部完成！最终结果已保存在: {output_file}")

if __name__ == "__main__":
    main()