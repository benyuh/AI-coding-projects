# 商品信息提取工具 | Product Info Extractor

> 用 Gemini Vision API 批量分析商品详情页图片，自动提取结构化信息。
>
> Use Gemini Vision API to batch-analyze product detail images and extract structured data.

## 背景 | Background

竞品调研或选品时需要快速整理大量商品信息，人工翻图效率极低。本工具通过 AI 视觉理解，自动从图片中提取商品名、价格、卖点等字段，输出为 JSON 供后续分析。

## 支持品类 | Supported Categories

| 脚本 | 品类 | 提取字段 |
|------|------|---------|
| `task_antisun.py` | 防晒霜 | 商品名、价格、SPF/PA值、质地、核心卖点 |
| `task_bodycream.py` | 身体乳 | 商品名、价格、功效、质地、卖点 |
| `task_bodywash.py` | 沐浴露 | 商品名、价格、香型、功效、卖点 |

## 使用方法 | Usage

**1. 安装依赖**
```bash
pip install google-generativeai Pillow
```

**2. 配置 API Key**

在脚本顶部将 `YOUR_GEMINI_API_KEY_HERE` 替换为你的 Gemini API Key：
```python
API_KEY = "YOUR_GEMINI_API_KEY_HERE"
```

> 获取方式：访问 [Google AI Studio](https://aistudio.google.com) 创建 API Key

**3. 准备图片**

将商品详情页图片放入对应目录：
```
images/
  antisun/     ← 防晒霜图片
  bodycream/   ← 身体乳图片
  bodywash/    ← 沐浴露图片
```

**4. 运行**
```bash
cd python/
python task_antisun.py
```

结果输出至 `results/antisun_data.json`。

## 输出示例 | Output Example

```json
{
  "product_name": "安耐晒小金瓶防晒乳",
  "price": 189,
  "sun_protection": "SPF50+ PA++++",
  "texture": "乳液",
  "selling_points": ["防水防汗", "敏感肌可用", "成膜快"],
  "file_name": "001.jpg",
  "category": "antisun"
}
```

## 扩展新品类 | Add New Category

复制任意一个 `task_*.py`，修改顶部的 `CATEGORY_NAME` 和 `PROMPT_TEXT` 即可适配新品类。
