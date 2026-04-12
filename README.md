# AI Coding Projects

> 搜索广告产品经理的 AI 编程实践：用 AI 辅助编码解决真实工作问题，无需系统编程背景。
>
> AI-assisted coding projects by a Search Ads PM — solving real work problems without a formal engineering background.

---

## 项目列表 | Projects

### 1. Query 意图分类工具 | Query Intent Classifier
**状态 | Status:** ✅ 完成

**背景 | Background:**
新产品上线时需要对大量搜索 Query 进行意图分类，人工标注效率低，用 AI 批量打分可大幅提速。When launching new ad products, manual query categorization is too slow — AI batch scoring dramatically improves efficiency.

**功能 | Features:**
- 两维度打分：多商品意图 & 多机构意图（0-10 分）
- 批量处理：每批 50 条，支持断点续传
- 多模型轮换：自动探测可用 Gemini 模型，限流时自动切换
- 错误处理：解析失败自动拆批重试，失败日志记录

**技术栈 | Tech Stack:** Python · Gemini CLI

**文件 | Files:**
- `query_identity.py` — 主处理脚本

**使用方法 | Usage:**
```bash
# 前提：已安装并登录 Gemini CLI
python query_identity.py
```

---

### 2. 商品信息提取工具 | Product Info Extractor
**状态 | Status:** 🚧 规划中

---

### 3. 自动图片裁剪工具 | Auto Image Cropper
**状态 | Status:** 🚧 规划中

---

## 经验与反思 | Lessons Learned

1. **模型选择**：简单分类任务用轻量模型（gemini-2.5-flash-lite）效果已很好，一开始用旗舰模型反而容易超限；降到 fast 模型后效果好了很多。Simple tasks don't need powerful models — lighter variants perform better in practice.

2. **批次大小**：30-50 条最稳定，100 条容易截断导致解析失败。Batch size 30-50 is most stable; 100 causes frequent truncation.

3. **工程健壮性**：2 万条数据必须做断点续传和失败日志，否则中途崩溃代价极高。For large-scale jobs, checkpoint recovery and failure logging are non-negotiable.

4. **认证问题**：Gemini CLI 会周期性弹出重新认证，大规模任务建议考虑接入官方 API 或低价替代模型（如 DeepSeek）。Gemini CLI requires periodic re-auth — for production-scale tasks, consider the official API or cost-effective alternatives.

---

## 开发理念 | Philosophy

> "用最小的技术投入解决真实的工作问题。"
>
> "Minimum technical overhead, maximum real-world impact."

---

## 关于作者 | About

搜索广告产品经理，SQL 和数据分析背景，近期通过 AI 辅助编程（Claude Code、Gemini CLI）解决工作中的自动化需求。

Search Ads PM with SQL/data analysis background, recently using AI-assisted coding (Claude Code, Gemini CLI) to automate real work problems.

🔗 [Search Ads Knowledge](https://github.com/benyuh/search-ads-knowledge) | [Data Analysis Notes](https://github.com/benyuh/data-analysis-notes) | [AI Monetization](https://github.com/benyuh/ai-monetization)
