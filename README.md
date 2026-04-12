# AI Coding Projects

> 搜索广告产品经理的 AI 编程实践：用 AI 辅助编码解决真实工作问题，无需系统编程背景。
>
> AI-assisted coding projects by a Search Ads PM — solving real work problems without a formal engineering background.

---

## 项目列表 | Projects

### 1. Query 意图分类工具 | Query Intent Classifier
**目录 | Path:** [`query-classifier/`](./query-classifier/)  
**状态 | Status:** ✅ 完成

批量对搜索 Query 进行意图打分（多商品意图 & 多机构意图），用于新广告产品上线时的 Query 圈选。基于 Gemini CLI，支持断点续传、多模型轮换、限流自动退避。

Batch-score search queries on two intent dimensions for new ad product launches. Built on Gemini CLI with checkpoint recovery, model rotation, and rate-limit handling.

**技术栈：** Python · Gemini CLI

---

### 2. 广告图片自动合成工具 | Ad Image Maker
**目录 | Path:** [`ad-image-maker/`](./ad-image-maker/)  
**状态 | Status:** ✅ 完成

从 URL 列表批量下载商品图片，自动合成到广告模板，一次生成 3 种尺寸（3:1 / 1:1 / 3:2）。内置两种填充策略：居中裁切 & 高斯模糊填充。

Batch download product images and auto-composite into ad templates across 3 sizes. Two fill strategies: center-crop and Gaussian blur padding.

**技术栈：** Python · Pillow

---

### 3. 商品信息提取工具 | Product Info Extractor
**目录 | Path:** [`product-info-extractor/`](./product-info-extractor/)  
**状态 | Status:** ✅ 完成

用 Gemini Vision API 批量分析商品详情页图片，自动提取结构化信息（商品名、价格、卖点等），输出 JSON。支持多品类：防晒、身体乳、沐浴露等。

Use Gemini Vision API to batch-extract structured product info (name, price, selling points) from detail page images. Multi-category support.

**技术栈：** Python · Gemini API (Vision)

---

## 经验与反思 | Lessons Learned

1. **模型选择**：简单任务用轻量模型（gemini-flash-lite）效果已很好，不需要旗舰模型，且速度更快、更少超限。
2. **批次大小**：Query 批处理 30-50 条最稳，100 条容易截断。
3. **工程健壮性**：大批量任务必须做断点续传 + 失败日志，否则中途崩溃代价高。
4. **API Key 安全**：不要把 Key 硬编码在代码里，用环境变量或配置文件隔离。

---

## 开发理念 | Philosophy

> "用最小的技术投入解决真实的工作问题。"  
> "Minimum technical overhead, maximum real-world impact."

---

## 关于作者 | About

搜索广告产品经理，SQL 和数据分析背景，通过 AI 辅助编程（Claude Code、Gemini CLI/API）解决工作中的自动化需求。

Search Ads PM with SQL/data analysis background, using AI-assisted coding to automate real workflows.

🔗 [Search Ads Knowledge](https://github.com/benyuh/search-ads-knowledge) · [Data Analysis Notes](https://github.com/benyuh/data-analysis-notes) · [AI Monetization](https://github.com/benyuh/ai-monetization)
