import os
import requests
from PIL import Image, ImageOps, ImageFilter
from io import BytesIO

# ================= V6 高斯模糊策略版 (使用 V5 坐标) =================

CONFIG = {
    # 第一组：3:1 (横幅)
    # 坐标: 右下角向右+2 -> (770)
    'banner_3_1': {
        'bg_file': 'bg_3_1.png',  
        'box': (23, 262, 770, 511) 
    },
    
    # 第二组：1:1 (方形)
    # 坐标: 左上(157)，右下(262, 396)
    'square_1_1': {
        'bg_file': 'bg_1_1.png',  
        'box': (26, 157, 262, 396) 
    },
    
    # 第三组：3:2 (竖卡)
    # 坐标: 右下(388, 353)
    'card_other': {
        'bg_file': 'bg_3_2.png',  
        'box': (26, 112, 388, 353) 
    }
}

URL_FILE = 'urls.txt'      
OUTPUT_DIR = 'output'      

# ===========================================

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def download_image(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return Image.open(BytesIO(response.content)).convert('RGB')
        return None
    except Exception as e:
        print(f"  [下载失败] {e}")
        return None

def create_blur_padding_image(material_img, target_w, target_h):
    """
    核心策略函数：生成高斯模糊背景 + 完整前景
    """
    # 1. 制作模糊背景 (Background)
    # 先把图撑满整个区域 (会有裁切)，作为底图
    bg = ImageOps.fit(material_img, (target_w, target_h), method=Image.Resampling.LANCZOS)
    # 应用高斯模糊 (Radius=15 模糊程度适中，可调大)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=15))
    
    # 2. 制作清晰前景 (Foreground)
    # 计算缩放比例，保证图片完整放入 (Contain)，不裁切
    src_w, src_h = material_img.size
    ratio = min(target_w / src_w, target_h / src_h)
    new_w = int(src_w * ratio)
    new_h = int(src_h * ratio)
    
    foreground = material_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    # 3. 居中合成
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    
    # 把前景贴在模糊背景上
    bg.paste(foreground, (paste_x, paste_y))
    
    return bg

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
        
        # === 核心修改：调用高斯模糊填充策略 ===
        final_slot_img = create_blur_padding_image(material_img, slot_w, slot_h)
        
        # 粘贴回底图
        base_img.paste(final_slot_img, (box[0], box[1]))
        
        output_filename = f"{index:03d}_{template_key}.jpg"
        save_path = os.path.join(OUTPUT_DIR, output_filename)
        base_img.save(save_path, quality=95)
        print(f"    -> 合成成功 (模糊填充): {output_filename}")
        
    except Exception as e:
        print(f"    [异常] {template_key}: {e}")

def main():
    print("=== 开始运行 (V6 高斯模糊版 - 跑前20个) ===")
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
            # 1. 保存原图 (方便对比)
            original_filename = f"{idx:03d}_original.jpg"
            org_path = os.path.join(OUTPUT_DIR, original_filename)
            material.save(org_path, quality=95)
            
            # 2. 生成3种尺寸
            for key, conf in CONFIG.items():
                process_single_template(material, key, conf, idx)
        else:
            print("    [跳过] 图片无法下载")

    print("\n=== 前20组处理完毕！请检查 output 文件夹 ===")

if __name__ == "__main__":
    main()
