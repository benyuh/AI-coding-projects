# 长文转信息图 · Long Text to Infographic

将任意长文自动转换为适合社交媒体传播的 PNG 信息图（小红书/微信朋友圈）。

> AI-powered pipeline that converts long-form articles into shareable infographic images for Chinese social media platforms.

---

## 效果示例 | Demo

输入一篇长文 → 自动生成多张卡片信息图，支持 10 种内容结构 × 2 平台 × 5 视觉风格。

---

## 项目架构 | Architecture

```
输入文本
  → Agent 1 Router          路由决策 + 风险预检（拒绝法律/医疗等内容）
  → Agent 2 内容理解         分块 + Claim 抽取 + 卡片预算计算（3-15 张）
  → Agent 3 Orchestrator
      → Worker A 文案        title/body 生成，标题党检测
      → Worker B 事实核验    数字/时间/实体别名规则校验
      → Worker C 视觉结构    visual_type 决策，多样性约束
      → Worker D 风格选择    结构-风格矩阵查表
  → Blueprint IR             Pydantic 完整数据契约
  → Gate 1 蓝图评估          4 维并行 LLM 打分（信息完整度/语义忠实度等）
  → Tool 1 图片渲染           Jinja2 模板 + Playwright 截图 + PIL 水印
  → Gate 2 渲染评估          文字溢出/字号/OCR/Vision LLM/事实漂移
  → PNG 输出
```

**服务层（v3.1.5）：** FastAPI HTTP API，支持异步任务提交和状态查询。

---

## 技术栈 | Tech Stack

| 类别 | 工具 |
|------|------|
| AI 模型 | Anthropic Claude (claude-3-5-sonnet / claude-3-haiku) |
| Agent 编排 | LangGraph StateGraph |
| 数据模型 | Pydantic v2 |
| 渲染 | Jinja2 + Playwright + Pillow |
| 服务层 | FastAPI + Uvicorn |
| 测试 | pytest + pytest-asyncio |
| 配置 | YAML |

---

## 目录结构 | Structure

```
longtext-image-gen/
├── main.py                  # CLI 入口
├── render.py                # 渲染逻辑（Legacy v3.1.0）
├── render.html              # 基础 HTML 模板（Legacy v3.1.0）
├── prompt.py                # Prompt 模板
├── requirements.txt         # 依赖
├── batch_create_cases.py    # 批量测试用例生成工具
│
├── agents/                  # Multi-Agent 层
│   ├── agent0_multisource.py  # 多源聚合（v3.2+）
│   ├── agent1_router.py       # 路由 + 风险预检
│   ├── agent2_understand.py   # 内容理解 + 卡片预算
│   └── agent3_orchestrate.py  # 并发编排
│
├── workers/                 # Worker 层
│   ├── worker_a_copy.py       # 文案生成
│   ├── worker_b_factcheck.py  # 事实核验（纯规则）
│   ├── worker_c_visual.py     # 视觉结构决策
│   └── worker_d_style.py      # 风格选择（纯规则）
│
├── gates/                   # 质量 Gate 层
│   ├── gate1_blueprint.py     # 蓝图质量评估（4 维 LLM）
│   └── gate2_render.py        # 渲染质量检查（OCR + Vision）
│
├── orchestrator/            # 编排层
│   ├── graph.py               # LangGraph 主图（11 节点）
│   └── retry_budget.py        # 三层重试预算
│
├── ir/                      # 中间表示层
│   └── models.py              # 全套 Pydantic 数据契约
│
├── render/                  # 渲染层
│   ├── templates/             # 10 种结构 × 2 平台 = 20 个 Jinja2 模板
│   ├── styles/                # 5 套风格 YAML
│   ├── watermark.py           # PIL 水印后处理
│   └── playwright_pool.py     # 浏览器实例池
│
├── service/                 # HTTP 服务层（v3.1.5）
│   ├── app.py                 # FastAPI 主应用
│   ├── schemas.py             # 请求/响应模型
│   └── job_store.py           # 内存任务状态存储
│
├── llm/                     # LLM 客户端封装
├── infra/                   # 基础设施（config/tracing/deps）
├── tools/                   # 工具层（Tool1 图片渲染）
├── configs/                 # 配置文件（关键词/实体别名/平台参数）
├── evals/                   # 评估框架（datasets + reports）
├── tests/                   # 测试套件（61 passed, 1 skipped @ v3.1.5）
└── docs/                    # 设计文档
    ├── product_prd.md
    ├── tech_design.md
    ├── ROADMAP.md
    └── CHANGELOG.md
```

---

## 快速开始 | Quick Start

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置 API Key

```bash
export ANTHROPIC_API_KEY=your_key_here
```

### 3. 运行（CLI 模式）

```bash
# 从文件读取
python main.py --file article.txt --output result.png

# 直接传入文本
python main.py --text "你的长文内容..." --output result.png

# 跳过 Gate（调试用）
python main.py --file article.txt --no-gates
```

### 4. 运行（API 服务模式，v3.1.5）

```bash
uvicorn service.app:app --reload

# 提交任务
curl -X POST http://localhost:8000/render \
  -H "Content-Type: application/json" \
  -d '{"text": "你的长文内容（≥100字）..."}'

# 查询状态
curl http://localhost:8000/status/{job_id}

# 下载图片
curl http://localhost:8000/download/{job_id} -o result.png
```

### 5. 运行测试

```bash
pytest tests/ -v
```

---

## 版本历史 | Versions

| 版本 | 状态 | 主要特性 |
|------|------|---------|
| v3.1.0 | ✅ 完成 | Pre-P0 极简验证，4 文件，单 prompt → PNG |
| v3.1.1 | ✅ 完成 | 渲染精修 + 可观测性（token/耗时统计） |
| v3.1.2 | ✅ 完成 | 完整多 Agent Pipeline + Blueprint IR |
| v3.1.3 | ✅ 完成 | Gate 1/2 质量闭环 + LangGraph 编排 |
| v3.1.4 | ✅ 完成 | 20 模板 + 5 风格 + Chart.js/D3 + 水印 |
| v3.1.5 | ✅ 完成 | FastAPI 服务层，61 测试全通过 |
| v3.2+ | 规划中 | 多源聚合、视频渲染、更多平台 |

详见 [docs/CHANGELOG.md](docs/CHANGELOG.md) 和 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

## 关于作者 | About

搜索广告产品经理，SQL 和数据分析背景，通过 AI 辅助编程解决工作自动化需求。

本项目使用 Anthropic Claude + Ducc (百度 AI 编程 Agent) 辅助开发。
