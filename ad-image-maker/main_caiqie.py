import os
import requests
from PIL import Image, ImageOps
from io import BytesIO

# ================= V5 坐标最新修正版 (裁切版) =================

CONFIG = {
    # 第一组：3:1 (对应 bg_3_1.png)
    # 最新修正：右下角向右+2 -> (770)
    'banner_3_1': {
        'bg_file': 'bg_3_1.png',  
        'box': (23, 262, 770, 511) 
    },
    
    # 第二组：1:1 (对应 bg_1_1.png)
    # 最新修正：左上(上移3->157)，右下(右移3->262，下移5->396)
    'square_1_1': {
        'bg_file': 'bg_1_1.png',  
        'box': (26, 157, 262, 396) 
    },
    
    # 第三组：3:2 (对应 bg_3_2.png)
    # 最新修正：右下(右移14->388，上移9->353)
    'card_other': {
        'bg_file': 'bg_3_2.png',  
        'box': (26, 112, 388, 353) 
    }
}

URL_FILE = 'urls.txt'      
# === 修改点：将输出文件夹改为 output_caiqie ===
OUTPUT_DIR = 'output_caiqie'      

# ===========================================

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def download_image(url):
    try:
        # 设置10秒超时
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return Image.open(BytesIO(response.content)).convert('RGB')
        return None
    except Exception as e:
        print(f"  [下载失败] {e}")
        return None

def process_single_template(material_img, template_key, config_item, index):
    bg_filename = config_item['bg_file']
    
    if not os.path.exists(bg_filename):
        print(f"  [错误] 找不到底图: {bg_filename}")
        return

    try:
        base_img = Image.open(bg_filename).convert('RGB')
        
        box = config_item['box']
        slot_w = box[2] - box[0]
        slot_h = box[3] - box[1]
        
        # 居中裁切策略
        cropped_img = ImageOps.fit(
            material_img, 
            (slot_w, slot_h), 
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5) 
        )
        
        base_img.paste(cropped_img, (box[0], box[1]))
        
        output_filename = f"{index:03d}_{template_key}.jpg"
        save_path = os.path.join(OUTPUT_DIR, output_filename)
        base_img.save(save_path, quality=95)
        print(f"    -> 合成成功: {output_filename}")
        
    except Exception as e:
        print(f"    [异常] {template_key}: {e}")

def main():
    print(f"=== 开始运行 (裁切版) -> 输出至 {OUTPUT_DIR} ===")
    ensure_dir(OUTPUT_DIR)
    
    if not os.path.exists(URL_FILE):
        print(f"找不到 {URL_FILE}。")
        return

    with open(URL_FILE, 'r') as f:
        all_urls = [line.strip() for line in f if line.strip()]

    if not all_urls:
        print("urls.txt 是空的！")
        return

    # === 设置运行数量：20 ===
    target_count = 20
    run_urls = all_urls[:target_count]
    
    print(f"总链接 {len(all_urls)} 个，本次将处理前 {len(run_urls)} 个。\n")

    for i, url in enumerate(run_urls):
        idx = i + 1
        print(f"[{idx}/{len(run_urls)}] 处理中...")
        
        material = download_image(url)
        
        if material:
            # 1. 保存原图
            original_filename = f"{idx:03d}_original.jpg"
            org_path = os.path.join(OUTPUT_DIR, original_filename)
            material.save(org_path, quality=95)
            
            # 2. 生成3种尺寸
            for key, conf in CONFIG.items():
                process_single_template(material, key, conf, idx)
        else:
            print("    [跳过] 图片无法下载")

    print(f"\n=== 前20组处理完毕！请检查 {OUTPUT_DIR} 文件夹 ===")

if __name__ == "__main__":
    main()
