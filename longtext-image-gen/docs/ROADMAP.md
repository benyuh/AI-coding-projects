# 长文转信息图/视频 —— 版本路线图 v2

> 制定日期：2026-04-28
> 基于：product_prd、tech_design.md、当前 Pre-P0 实现状态
> 原则：每个版本都可独立运行、独立验收；每步都在上一步基础上增量叠加

---

## 版本全景

```
v3.1.0  Pre-P0 极简验证版（已完成 ✅）
  单 prompt → 单 agent → Playwright 渲染 → PNG
  4 个文件，能跑通，能出图，但不等同于 tech_design 的完整 P0

v3.1.1  渲染精修 + 可观测性
  在现有基础样式上继续提升视觉层级、溢出防护、平台适配
  让运行"可看"：token 统计、耗时打印、JSON 防御校验

v3.1.2  完整 Pipeline 骨架（无 Gate）
  重构为 tech_design 要求的多 Agent 架构：
  Agent 1 Router → Agent 2 理解 → Agent 3 + Workers A/B/C/D
  输出 Blueprint IR，再交 Tool 1 渲染
  还没有 Gate，不做质量评估

v3.1.3  Gate 1 + Gate 2（质量闭环）
  接入 Gate 1 蓝图评估（4 维 LLM 打分）
  接入 Gate 2 渲染评估（OCR + Vision LLM + DOM 检测）
  接入重试预算机制（render_only/blueprint_level/fact_drift 分层计数）

v3.1.4  模板多样化（10 种结构 × 2 平台 × 5 风格）
  补全全量模板库：10 套结构 × 小红书 + 朋友圈 = 20 个模板
  补全 5 套风格 YAML（含完整 6 类参数）
  接入 Chart.js / D3（data_chart / timeline / entity_graph 组件）

v3.1.5  tech_design P0 完整版
  FastAPI 服务层（HTTP API + SSE 流式状态）
  PostgreSQL + Redis + MinIO 基础设施接入
  Prometheus + Grafana 可观测性全量上线
  完整测试套件（unit + integration + eval）
  达到 tech_design P0 范围，系统可对外发布

v3.2+  P1 能力扩展
  多源聚合、视频渲染、更多平台模板、二维码/生成标识、Voting 增强
```

---

## v3.1.0 — Pre-P0 极简验证版（已完成 ✅）

### 实现状态

4 个文件，单 prompt + Playwright 渲染，能跑通：

```
main.py      ← LLM 调用 + CLI 入口
prompt.py    ← System Prompt + User Prompt 模板
render.py    ← Jinja2 渲染 + Playwright 截图
render.html  ← 单一 HTML 模板（5 种 card_type）
```

### 已验证

- 内置测试长文（中国AI大模型报告）能成功出图
- `--file` / `--text` / `--output` 参数均可用
- `output_20260428_230514.png`、`output_policy_test.png` 已生成

### 不足（留给后续版本）

- 已有 cover/data/timeline/summary 的基础视觉差异，但还缺少更强视觉层级、溢出防护和平台适配
- 无 token 统计、无耗时日志
- 无 JSON 防御校验，LLM 输出字段缺失、长度超限、卡片数异常时缺少稳定兜底
- 卡片数量仍由 prompt 固定为 5-12 张，尚未对齐 PRD 4.3.2 的 3-15 张动态预算规则
- 无 Agent 拆分，无 Blueprint IR，无质量 Gate

---

## v3.1.1 — 渲染精修 + 可观测性

### 目标

> 不改变 4 文件架构，在现有基础样式上继续提升输出质量，补齐可观测性和本地防御校验。

### 工作包

#### 1. render.html — 视觉精修与溢出防护

当前模板已经具备 cover/data/timeline/summary 的基础差异化，本版本不再从零建立样式，而是做精修：

| card_type | 精修要求 |
|-----------|---------|
| cover | 强化封面层级，主标题字号、留白、背景装饰更接近社交媒体封面 |
| data | 保持 `data_label` 大字优势，补充超长数字/百分比的换行与缩放策略 |
| timeline | 强化时间徽章与轴线层级，避免时间字段缺失时出现空白结构 |
| section | 优化标题、正文、items 的行高和间距，降低长文本溢出概率 |
| summary | 与 cover 形成风格呼应，同时保持总结卡收尾感 |

#### 2. main.py — 可观测性增强

利用 Anthropic SDK 的 `message.usage` 字段，输出：
```
[LLM] 耗时: 8.3s | input: 1240 tokens | output: 892 tokens
[渲染] 耗时: 2.1s
[总计] 端到端: 10.4s | 输出: output_xxx.png (234 KB)
```

#### 3. main.py — JSON 防御校验

LLM 响应后、渲染前做本地字段检查（不依赖新库）：
- `cards` 字段存在且非空（< 2 张提前报错）
- 每张卡 `card_type` 必须在 5 种枚举内，否则降级为 `section`
- `title` 超 18 字时截断，`body` 超 80 字时截断
- `data` 类型 `data_label` 为空时用 `body` 头部补填

#### 4. main.py — max_tokens 截断修复

当 `stop_reason == "max_tokens"` 时，尝试在 raw JSON 末尾补 `]}` 做修复性解析，而非直接失败。

### 验收标准

1. `data` 卡的数字大字明显比 `section` 卡更突出，且超长数字不破版
2. `cover` 卡有封面感，`summary` 卡有收尾感，视觉层级明显优于 v3.1.0
3. 控制台输出 token 消耗和各阶段耗时
4. LLM 返回 `body > 80 字` 时，渲染不溢出（已截断）
5. `cards < 2` 时报友好错误，不产出空白图

---

## v3.1.2 — 完整 Pipeline 骨架（无 Gate）

### 目标

> 重构为 tech_design 要求的多 Agent 架构，实现 Blueprint IR 数据契约，
> 但暂时跳过 Gate 1/2，让主流程先跑通。

### 架构变化

```
v3.1.1（4 文件）:
  main.py → LLM → cards_data → render.html → PNG

v3.1.2（完整 Pipeline，无 Gate）:
  API 入口
    → Agent 1 Router（文章类型/用户画像/风险预检）
    → Agent 2 内容理解（分块/claim/card_slots/结构推荐）
    → Agent 3 Orchestrator
        → Worker A 文案（title/body）
        → Worker B 事实核验（规则，无 LLM）
        → Worker C 视觉结构（visual_spec）
        → Worker D 风格选择（style_tokens，无 LLM）
    → Blueprint IR（完整 Pydantic 模型）
    → Tool 1 图片渲染（Jinja2 + Playwright）
    → PNG
```

### 主要工作

**1. 建立 `ir/models.py`**

按 tech_design §2.2 实现全套 Pydantic 数据契约：
`SourceBundle` / `RouterDecision` / `ContentTree` / `Blueprint` / `Card` / `VisualSpec` / `BCheck` / `StyleTokens` / `RenderArtifact`

**2. 实现 Agent 1 Router（`agents/agent1_router.py`）**

- 预检短路（< 200 字 / > 30000 字 → skip）
- 风险内容拒绝（法律/医疗/诗歌/PII → blocked）
- LLM 一次调用，输出 `RouterDecision` JSON
- 缓存键：`hash(text) + profile_hash`

**3. 实现 Agent 2 内容理解（`agents/agent2_understand.py`）**

- 按 PRD 4.3.1 分块（< 3000 字不分块，3000-10000 字按标题/2000字，以此类推）
- 每块并发调用 LLM 抽取 `Claim` + `FactElement`（含字符 span）
- 卡片预算决策对齐 PRD 4.3.2：按原文长度、信息密度、用户类型、多源冲突动态计算 3-15 张卡片
- 将 `prompt.py` 中固定 5-12 张的临时规则迁移到 Agent 2 的预算决策，不再由单 prompt 独立决定卡片数
- 输出 `ContentTree`（含 card_slots 规划）

**4. 实现 Worker A 文案（`workers/worker_a_copy.py`）**

- 区分 professional / general 两套 prompt 模板
- 字数硬约束（title ≤ 18，body ≤ 80）
- 标题党关键词检查（阶段 1：`clickbait_keywords.yaml` 正则）
- 输出 `CardContent`

**5. 实现 Worker B 事实核验（`workers/worker_b_factcheck.py`）**

- 纯规则，不调 LLM
- 正则抽取数字/时间 → 字符串精确匹配
- NER（v3.1.2 先用字典 `entity_alias.yaml`，接口 forward-compatible）
- 驳回回 Worker A，单卡上限 2 次，仍失败降级 `text_only`

**6. 实现 Worker C 视觉结构（`workers/worker_c_visual.py`）**

- 按 PRD 4.5.3 决策树选 `visual_type`（8 种枚举）
- 构建 `structured_data`（timeline 节点数组、comparison_table 行列、flow_diagram 节点+边）
- 全局规则 G1：`text_with_icon` 占比 ≤ 60%，同类型连续 ≤ 3

**7. 实现 Worker D 风格选择（`workers/worker_d_style.py`）**

- 纯规则，不调 LLM
- 从 PRD 4.4.2 结构-风格兼容矩阵查表
- 加载 `render/styles/{style_id}.yaml`，按平台微调
- 输出 `StyleTokens`

**8. 实现 Agent 3 Orchestrator（`agents/agent3_orchestrate.py`）**

- `asyncio.Semaphore` 并发控制（Worker A ≤ 8 并发）
- A→B 串行，C/D 与 A 并行
- 收集所有 Worker 输出，组装 `Blueprint`

**9. 重构 Tool 1（`tools/tool1_image_render.py`）**

- 接收 `Blueprint` 而非裸 JSON
- 模板选择：`templates/{narrative_structure}/{platform}.html`
- CSS 变量从 `StyleTokens` 注入
- 水印叠加（PIL）

**10. 目录结构重构**

```
longtext_v3.1/
├── agents/        ← agent1/2/3
├── workers/       ← worker_a/b/c/d
├── tools/         ← tool1
├── ir/            ← models.py（全套 Pydantic）
├── llm/           ← client.py（统一封装，自动重试，缓存）
├── render/
│   ├── playwright_pool.py
│   ├── styles/    ← 5 套 YAML（v3.1.2 先补 clean_business 骨架）
│   └── templates/ ← pyramid_argument/xiaohongshu.html（先 1 套）
├── infra/
│   ├── config.py / deps.py
│   └── tracing.py（@trace 装饰器，打印耗时+token）
├── configs/
│   ├── clickbait_keywords.yaml
│   ├── entity_alias.yaml
│   └── platform_scaling.yaml
├── main.py        ← CLI 入口（调 pipeline）
└── tests/unit/test_smoke.py
```

### 验收标准

1. 给定任意长文，能走完 Agent1→2→3→Worker A/B/C/D→Tool1 全流程，输出 PNG
2. `RouterDecision` 能正确拒绝法律/医疗/诗歌类文本
3. 每张卡片的 `b_check.status` 为 `passed` 或 `degraded`（不存在未校验卡片）
4. Blueprint 中 `visual_type` 至少有 2 种不同类型（G1 初步约束）
5. 卡片数量符合 PRD 4.3.2 的动态预算范围，而不是固定 5-12 张
6. 日志中每个节点有耗时打印（`@trace` 生效）

---

## v3.1.3 — Gate 1 + Gate 2（质量闭环）

### 目标

> 在 v3.1.2 Pipeline 基础上，接入两道质量 Gate，建立重试预算机制，
> 让系统具备"自我修复"能力。

### 主要工作

**1. 实现 Gate 1 蓝图评估（`gates/gate1_blueprint.py`）**

按 PRD 4.7.3 的 4 个维度实现并行 LLM 评估；v3.1.x 默认每个维度单次调用，先不启用 Voting，以控制成本并优先校准评分逻辑：

| 维度 | 阈值 | 输入 | 失败回退 |
|------|------|------|---------|
| 信息完整度 | ≥ 85 | 原文 outline + Blueprint title/body | → Agent 2 |
| 语义忠实度 | ≥ 90 | claim evidence_span + 卡片 body | → Worker A |
| 卡片合理性 | ≥ 80 | Blueprint 整体 + 跨卡矛盾检测 | → Agent 3 |
| 受众适配度 | ≥ 75 | 文案术语密度 vs user_type | → Worker A |

关键：语义忠实度必须真实传入 `evidence_span.context_50`，不可占位。

**2. 标题党两阶段判定（完整实现）**

- 阶段 1 已在 v3.1.2 Worker A 实现（关键词正则）
- 阶段 2：未命中的标题批量打包，单次 LLM 调用，输出 `clickbait_score` + `verdict` + `rewrite_suggestion`
- borderline 标记 `title_warning`，扣 Gate 1 卡片合理性 5 分/张

**3. 实现 Gate 2 渲染评估（`gates/gate2_render.py`）**

按 PRD 4.11.2 五项检查：

| 检查 | 实现 |
|------|------|
| 文字溢出 | Playwright 注入 JS，检查 `scrollWidth > clientWidth` |
| 字号可读性 | 解析 StyleTokens，断言渲染后实际字号 ≥ 18px |
| 错别字 | OCR（PaddleOCR）提取图中文字 vs Blueprint，Levenshtein ≥ 2 → fact_drift |
| 视觉合理性 | Vision LLM（Claude multimodal）打分 ≥ 75 |
| 事实漂移 | OCR 文字 vs Blueprint 数字/人名差异 → fact_drift |

**4. 实现重试预算（`orchestrator/retry_budget.py`）**

按 PRD 4.11.4–4.11.7：

```python
class RetryBudget:
    render_only: int = 0   # max 3
    blueprint_level: int = 0  # max 2
    fact_drift: int = 0    # max 1
    # 高层级回退清零低层级计数器
    # 任一层级用完 → 对应降级（L1/L2/L3）
```

**5. 接入 Orchestrator 主图（`orchestrator/graph.py`）**

LangGraph StateGraph 串联：
`Agent1 → Agent2 → Agent3 → Gate1 →（通过）→ Tool1 → Gate2 →（通过）→ 输出`
`Gate1 失败 → 回退路由 → 对应节点重跑`
`Gate2 失败 → 按 issue_type 分级回退`

**6. OCR 可观测性**

`infra/deps.py` 中：
- 有 PaddleOCR → 正常初始化
- 无 PaddleOCR → `log.warning("OCR 未初始化，Gate 2 OCR 检查跳过")` 不静默

### 验收标准

1. Gate 1 语义忠实度评分有真实 evidence_span 文本参与，不再是占位
2. Gate 1 日志明确标注当前为单次评分模式，Voting 未启用
3. 触发 `render_only` 回退后能自动重渲染，不崩溃
4. OCR 未安装时有明确日志，Gate 2 优雅降级而非静默跳过
5. `borderline` 标题在日志中可见，Blueprint 中有 `title_warning` 字段
6. 重试预算计数在日志中可见（每次回退打印计数器快照）

---

## v3.1.4 — 模板多样化（10 结构 × 2 平台 × 5 风格）

### 目标

> 补全全量模板库，让 10 种内容结构各有真实的专属视觉；
> 补全 5 套风格 YAML；接入数据可视化组件。

### 主要工作

**1. 建立 Jinja2 组件库（`render/templates/shared/components/`）**

| 组件文件 | 用途 |
|---------|------|
| `card_cover.html` | 封面大标题卡 |
| `card_data_highlight.html` | 大数字强调卡 |
| `card_timeline.html` | 竖向时间轴（CSS） |
| `card_comparison.html` | 左右对比表 |
| `card_text_icon.html` | 图标 + 文字卡 |
| `card_data_chart.html` | Chart.js 图表卡 |
| `card_entity_graph.html` | D3 关系图卡 |
| `card_flow_diagram.html` | CSS 步骤流程图 |

**2. 10 种结构 × 2 平台 = 20 个模板**

| 优先级 | 结构 | 小红书 | 朋友圈 |
|--------|------|--------|--------|
| 1 | pyramid_argument | ✅ v3.1.0 已有 | 补 wechat_moments |
| 2 | problem_solution_action | 新建 | 新建 |
| 3 | chronological_timeline | 新建 | 新建 |
| 4 | before_after_comparison | 新建 | 新建 |
| 5 | thought_journey | 新建 | 新建 |
| 6 | lin_style_explainer | 新建 | 新建 |
| 7 | pyramid_research_pro | 新建 | 新建 |
| 8 | entity_relation_map | 新建 | 新建 |
| 9 | consensus_disagreement_map | 新建 | 新建 |
| 10 | multi_source_timeline | 新建 | 新建 |

**3. 补全 5 种风格 YAML**

按 PRD 4.5.4 的完整参数体系，每种风格 6 类参数全部定义：

```
render/styles/
  clean_business.yaml       ← v3.1.2 已有骨架，补全所有参数
  xiaohongshu_warm.yaml     ← 新建
  data_journalism.yaml      ← 新建
  tech_minimal.yaml         ← 新建
  magazine_editorial.yaml   ← 新建
```

**4. 接入 Chart.js（data_chart 组件）**

- `card_data_chart.html` 内联 Chart.js CDN
- 数据来自 `VisualSpec.structured_data`，注入为 JSON data attribute
- 支持饼图（≤5 数据点）/ 柱状图（3-10）/ 折线图（≥5，时序）

**5. 接入 D3（entity_graph 组件）**

- `card_entity_graph.html` 用 D3 force layout
- 节点/边来自 `VisualSpec.structured_data.nodes + edges`
- 关系标签渲染在边的中点

**6. 水印 + 品牌标识（PIL 后处理）**

按 PRD 4.9.1.7：
- 右下角品牌水印（宽度 6%，透明度 60%）
- 多源场景：顶部信源标注 + 底部免责声明

### 验收标准

1. 10 种结构各能渲染出视觉上有差异的图（时间线有轴线，对比表有分栏，关系图有节点）
2. `data_chart` 类卡片有真实 Chart.js 图表渲染（饼/柱/折线自动选择）
3. 5 种风格在颜色/字体/圆角/背景上有明显视觉差异
4. 同一 Blueprint 切换风格可出 5 张视觉不同的图

---

## v3.1.5 — tech_design P0 完整版

### 目标

> 接入 FastAPI 服务层、基础设施（PG/Redis/MinIO）、完整可观测性，
> 补全测试套件，系统达到 tech_design P0 标准，可对外发布。

### 主要工作

**1. FastAPI 服务层（`api/`）**

按 tech_design §2.1 实现三个 API 端点：
- `POST /api/v1/generate` → 创建异步任务，返回 `job_id`
- `GET /api/v1/jobs/{id}/stream` → SSE 流式事件（每个 stage 状态推送）
- `GET /api/v1/jobs/{id}/artifacts` → 获取最终产物 URL

请求/响应严格对应 tech_design 的 Pydantic 模型。

**2. 基础设施接入（`infra/`）**

| 组件 | 用途 |
|------|------|
| PostgreSQL | jobs / blueprints / retries / 任务状态 |
| Redis | LLM 结果缓存（24h）/ 限流令牌桶 / 分布式锁 |
| MinIO（S3） | PNG / Blueprint JSON / 缩略图对象存储 |

`infra/db.py` / `infra/cache.py` / `infra/storage.py` 完整实现。

**3. LLMClient 完整封装（`llm/client.py`）**

按 tech_design §4.1：
- 自动重试（指数退避，429/5xx）
- `prompt + inputs + model` 三元组哈希做 Redis 24h 缓存
- 全局限流（Sonnet ≤ 8 RPS，Haiku ≤ 20 RPS）
- `schema` 参数强制结构化输出校验，失败最多重抽 2 次
- `voting` 参数支持（P0 默认 1 次，P1 按任务类型开启 3 次）

**4. Prometheus Metrics（`infra/metrics.py`）**

按 tech_design §5.4：
- `pipeline_duration_seconds` Histogram，按 stage
- `llm_tokens_total` Counter，按 model + direction
- `gate_pass_rate` Gauge，Gate1/Gate2 各维度
- `retry_count_total` Counter，按 issue_type
- `degradation_total` Counter，按 L1/L2/L3

暴露 `GET /metrics` 端点（Prometheus scrape）。

**5. 完整测试套件（`tests/`）**

```
tests/
├── unit/
│   ├── test_gate1_with_evidence.py      # Gate1 evidence_span 回归
│   ├── test_worker_a_clickbait.py       # 标题党两阶段判定
│   ├── test_worker_b_factcheck.py       # 数字/时间/NER 校验
│   ├── test_worker_c_diversity.py       # 视觉多样性约束 G1
│   └── test_retry_budget.py             # 三层重试计数逻辑
├── integration/
│   ├── test_pipeline_pyramid.py         # 完整 Pipeline pyramid_argument
│   └── test_pipeline_timeline.py        # chronological_timeline
└── eval/
    ├── baselines/                        # 10 篇基线文章（各类型 2 篇）
    └── test_eval_suite.py               # LLM-as-judge 四维打分
```

覆盖率目标：核心模块（agents/workers/gates）> 70%。

**6. 结构化错误码（`infra/errors.py`）**

```python
class ErrorCode(Enum):
    TEXT_TOO_SHORT = "TEXT_TOO_SHORT"
    TEXT_TOO_LONG = "TEXT_TOO_LONG"
    CONTENT_BLOCKED = "CONTENT_BLOCKED"
    CONTENT_UNDERSTAND_FAILED = "CONTENT_UNDERSTAND_FAILED"
    BLUEPRINT_GENERATION_FAILED = "BLUEPRINT_GENERATION_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
```

取代 `PipelineState.error` 的裸字符串，API 响应中携带 `error_code` 字段。

**7. Playwright Pool 完整化（`render/playwright_pool.py`）**

按 tech_design §5.1：
- 常驻 3 个浏览器实例（避免冷启动）
- 加锁取出/归还，崩溃自动重建
- `page.goto` 后等待 `networkidle` + `document.fonts.ready`

**8. 建立评估基线**

在 10 篇基线文章上跑 eval suite，记录：
- 四维评分（信息完整度/语义忠实度/卡片合理性/受众适配度）
- 渲染成功率
- 平均端到端耗时（P50/P95）
- 各 issue_type 触发频次

作为后续迭代的对比基准。

### 验收标准

1. `POST /api/v1/generate` → SSE 推送各 stage 状态 → `GET /artifacts` 返回图片 URL，全链路通
2. 所有单元测试通过，覆盖率 > 70%（核心模块）
3. 10 篇基线文章全部成功渲染（不触发 L3 全面降级）
4. Gate 1 四维平均评分达到各自阈值（85/90/80/75）
5. Grafana 看板能看到各节点 P50/P95 耗时、token 消耗、Gate 通过率
6. 系统在 4 并发 job 下稳定运行，P95 端到端 < 60s

---

## v3.2+ — P1 能力扩展

### 目标

> 在 v3.1.5 达到 tech_design P0 后，进入 P1：扩展输入来源、输出形态、平台模板和质量增强策略。

### 方向

**1. Agent 0 多源聚合**

- 支持多个网页、文档片段、AI 搜结果作为输入
- 实现语义去重、信源权威性排序、冲突保留和引用溯源
- 输出 `SourceBundle` 集合，并保留 `source_id` 到 claim/card 的链路

**2. Tool 2 视频渲染**

- 基于已生成的信息图卡片生成轮播式短视频
- 接入 TTS、字幕、转场和基础配乐
- 复用 Blueprint，避免重新理解原文

**3. 更多平台模板**

- 在小红书、朋友圈之后补充公众号长图、通用方图、微博/抖音封面等平台变体
- 将平台差异沉淀为尺寸、字号、密度和水印策略，而不是复制业务逻辑

**4. 二维码与生成标识**

- 补齐原文链接二维码、生成时间、render_id 后缀等 P1 元信息
- 对多源场景展示信源说明和免责声明

**5. Voting 与质量增强**

- 在 Gate 1 单次评分稳定后，再为高风险或高价值任务开启 Voting
- 默认策略从“全量开启”调整为“按任务类型、风险等级、预算动态开启”

### 验收标准

1. 多源输入能保留来源、去重和冲突信息，并生成可追溯 Blueprint
2. 同一 Blueprint 可输出图片和轮播视频两类产物
3. 至少新增 2 个非小红书平台模板并通过基线文章渲染
4. 二维码、生成标识、信源说明在对应场景下可控展示
5. Voting 可按配置开启，且日志能看到投票次数和最终判定

---

## 各版本文件变更范围汇总

| 版本 | 新增文件 | 修改文件 | 删除/重构 |
|------|---------|---------|---------|
| v3.1.0 | main.py / prompt.py / render.py / render.html | — | — |
| v3.1.1 | — | main.py / render.html / render.py | — |
| v3.1.2 | agents/ workers/ ir/ llm/ infra/（tracing） configs/ | main.py render/ | prompt.py（逻辑迁移进 agents） |
| v3.1.3 | gates/ orchestrator/ | agents/ workers/ tools/ | — |
| v3.1.4 | render/templates/ render/styles/ | tools/tool1 gates/gate2 | render.html（由模板系统替代） |
| v3.1.5 | api/ infra/（db/cache/storage/metrics） tests/ | 全部模块 | — |
| v3.2+ | agent0/ video/ render/platforms/ qrcode/ | api/ orchestrator/ gates/ render/ | — |

---

## 关键设计决策

### 为什么 v3.1.2 先跑通 Pipeline 再接 Gate？

Gate 1/2 都依赖 Blueprint IR 结构（`evidence_span`、`b_check` 等字段），
在 IR 数据契约稳定之前接入 Gate，会出现"评估的字段还不存在"的空转。
先让 v3.1.2 跑通、字段稳定，v3.1.3 接 Gate 才有意义。

### 为什么模板多样化放在 v3.1.4，而非更早？

模板依赖 Worker C 的 `visual_spec` 和 Worker D 的 `StyleTokens`。
在 v3.1.2 Workers 稳定输出这两个字段之前，写 20 套模板是在
"给一个还在变的数据结构写渲染"——会大量返工。

### 为什么 Gate 2 OCR 在 v3.1.3 而非 v3.1.5？

OCR 是 Gate 2 最关键的质量检查手段（错别字/事实漂移），
如果放到最后上线，前期迭代的模板质量就没有有效检验，会积累隐患。
v3.1.x 阶段 OCR 未安装时优雅降级（跳过 + 警告），不阻塞开发。

### Voting 为什么 v3.1.x 默认不开？

Voting = Gate 1 每维度调 3 次 LLM，费用 ×3。
v3.1.x 应先在单次调用下把评分逻辑调准（evidence_span 修复），
确认评分可信之后，再在 v3.2+ 按任务类型、风险等级、预算动态开启 Voting。

---

*本路线图随实际进展更新。每个版本完成验收后在版本号后标注完成日期。*
