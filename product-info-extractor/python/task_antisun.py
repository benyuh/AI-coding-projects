import google.generativeai as genai
import PIL.Image
import os
import json
import time

# ================= 配置区域 (下次换品类改这里即可) =================

# 1. 你的 API Key
API_KEY = "YOUR_GEMINI_API_KEY_HERE"

# 2. 当前品类名称 (必须和文件夹名字完全一致)
CATEGORY_NAME = "antisun"

# 3. 针对【防晒霜】的提取要求
PROMPT_TEXT = """
请分析这张商品详情页图片，提取以下关键信息。
请直接返回纯 JSON 格式数据，不要使用 Markdown 标记。

字段要求：
1. product_name: 商品名称（找字号最大的）。
2. price: 价格（数字）。
3. sun_protection: 防晒指数（关键！请提取 SPF值 和 PA值，如 "SPF50+ PA++++"）。
4. texture: 质地（如：乳液、喷雾、啫喱、摇摇乐、面霜质地等）。
5. selling_points: 核心卖点（如：防水防汗、美白、敏感肌可用、成膜快等）。

如果某个字段在图中完全找不到，请填 "未找到"。
"""

# ===============================================================

def main():
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-3-flash-preview')
    # 路径处理：脚本在 python/ 目录下，所以图片在 ../images/品类名
    base_dir = os.path.dirname(os.path.abspath(__file__)) # 获取脚本当前所在路径
    project_root = os.path.dirname(base_dir) # 获取项目根目录
    
    image_folder = os.path.join(project_root, "images", CATEGORY_NAME)
    output_folder = os.path.join(project_root, "results")
    output_file = os.path.join(output_folder, f"{CATEGORY_NAME}_data.json")

    # 自动创建 results 文件夹（如果不存在）
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 检查图片目录是否存在
    if not os.path.exists(image_folder):
        print(f"❌ 错误：找不到图片文件夹: {image_folder}")
        print(f"   请确认你在 'images' 文件夹里建立了 '{CATEGORY_NAME}' 子文件夹")
        return

    # 获取图片列表
    image_files = [f for f in os.listdir(image_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    total_files = len(image_files)

    if total_files == 0:
        print(f"❌ '{CATEGORY_NAME}' 文件夹里没有找到图片。")
        return

    print(f"🚀 正在处理品类 [{CATEGORY_NAME}]，共 {total_files} 张图片...")
    results = []

    for index, filename in enumerate(image_files):
        print(f"[{index+1}/{total_files}] 处理: {filename} ...", end="", flush=True)
        
        try:
            img_path = os.path.join(image_folder, filename)
            img = PIL.Image.open(img_path)

            response = model.generate_content([PROMPT_TEXT, img])
            
            clean_text = response.text.replace('```json', '').replace('```', '').strip()
            item_data = json.loads(clean_text)
            
            # 补充文件名和分类信息
            item_data['file_name'] = filename
            item_data['category'] = CATEGORY_NAME
            
            results.append(item_data)
            print(" ✅")

        except Exception as e:
            print(f" ❌ ({e})")
            results.append({"file_name": filename, "error": str(e)})

        time.sleep(10) # 稍微快一点，1.5秒间隔

    # 保存结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！结果已保存到: results/{CATEGORY_NAME}_data.json")

if __name__ == "__main__":
    main()