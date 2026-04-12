# 广告图片自动合成工具 | Ad Image Maker

> 批量下载商品素材图，自动合成到广告模板，一次生成多种尺寸。
>
> Batch download product images and auto-composite them into ad templates across multiple sizes.

## 背景 | Background

广告投放时需要为同一批商品生成多种尺寸的创意图（横幅 3:1、方形 1:1、竖卡 3:2），手动操作繁琐且容易出错。本工具通过读取图片 URL 列表，自动批量生成所有尺寸。

When running ads, the same product images need to be formatted into multiple creative sizes. This tool automates the entire process from URL list to finished creatives.

## 功能 | Features

- 从 `urls.txt` 批量读取商品图片 URL
- 自动下载并合成到 3 种广告模板尺寸：
  - 3:1 横幅（Banner）
  - 1:1 方形（Square）
  - 3:2 竖卡（Card）
- 两种填充策略：
  - **裁切版** (`main_caiqie.py`)：居中裁切，保证填满区域
  - **模糊填充版** (`main_mohu.py`)：保留完整图片 + 高斯模糊背景填充，适合比例差异大的素材

## 使用方法 | Usage

```bash
# 安装依赖
pip install Pillow requests

# 准备 urls.txt（每行一个图片 URL）
# 将模板底图（bg_3_1.png / bg_1_1.png / bg_3_2.png）放在同目录

# 运行裁切版
python main_caiqie.py

# 运行模糊填充版
python main_mohu.py
```

输出文件保存在 `output_caiqie/` 或 `output/` 目录。

## 效果对比 | Strategy Comparison

| 策略 | 适用场景 |
|------|---------|
| 居中裁切 | 素材比例接近目标尺寸时，画面饱满 |
| 高斯模糊填充 | 素材比例差异较大时，避免主体被裁掉 |
