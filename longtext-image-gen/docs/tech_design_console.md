# 长文转信息图 - 演示与监控控制台 技术设计（P0）

> 版本：v0.1（P0 草案）
> 日期：2026-04-29
> 关联文档：`tech_design.md`、`product_prd_workflow.png`
> 适用范围：本期仅交付 **P0**，覆盖单页 Demo + 实时监控的最小可用版本

---

## 0. 关键决策摘要

| 决策项 | 选择 | 原因 |
|---|---|---|
| 页面定位 | 混合型（演示 + 内部监控，开关切换） | 一套 UI 同时支撑对外 demo 与内部排错，避免维护两个前端 |
| 前端形态 | **单文件 HTML + 原生 EventSource + Tailwind CDN** | P0 零构建、双击可跑，与 FastAPI 同源部署，无需额外工具链 |
| 实时通信 | 复用现有 SSE `/api/v1/jobs/{id}/stream` | 后端已实现，前端 `EventSource` 原生支持，断线自动重连 |
| 渲染层 | 静态 HTML（`api/static/console.html`），由 FastAPI `StaticFiles` 挂载在 `/console` | 避免引入前端框架与构建产物，部署只多一个文件 |
| 表单契约 | 严格对齐 `GeneratePreferences` 的 Literal 枚举（含 `auto`） | 前端任何取值都必须能被 Pydantic 校验通过，杜绝 422 |
| 状态可视化 | **纵向节点图**（DOM 渲染，非 mermaid）；颜色编码 `idle/running/passed/degraded/failed/retrying` | 节点要"亮起来"、能展开看输入输出，mermaid 是静态的不满足 |
| 演示态 / 开发者态 | 顶部一个开关；开发者态多展示 token / 耗时 / 原始 JSON / 日志抽屉 | 演示翻车时一键切到详细模式排错 |
| 后端改动 | **仅丰富 SSE 事件字段** + 新增 `GET /api/v1/jobs/{id}/replay` | 不动流水线核心，最小侵入 |

---

## 1. 整体设计

### 1.1 系统位置

```
┌──────────────────────────────────────────────────────────┐
│ 浏览器                                                     │
│  ┌────────────────────────────────────────────────────┐   │
│  │ /console  (单文件 HTML)                            │   │
│  │  ├─ 左栏 输入区（表单 → POST /generate）           │   │
│  │  ├─ 中栏 流水线节点图（EventSource 订阅 SSE）      │   │
│  │  ├─ 右栏 产物区（渲染产物 + Blueprint 摘要）       │   │
│  │  └─ 底部 实时日志抽屉（开发者模式）                │   │
│  └────────────────────────────────────────────────────┘   │
└────────────┬─────────────────────────────────────────────┘
             │ HTTP / SSE
┌────────────▼─────────────────────────────────────────────┐
│ FastAPI (api/routes.py)                                   │
│  ├─ POST /api/v1/generate         （已存在）              │
│  ├─ GET  /api/v1/jobs/{id}/stream （已存在，事件需扩字段）│
│  ├─ GET  /api/v1/jobs/{id}/replay （新增，刷新恢复用）    │
│  └─ GET  /console                  （新增，托管静态页）   │
└──────────────────────────────────────────────────────────┘
```

### 1.2 P0 范围边界

P0 **只做**这些：

- 单源文本输入、生成图片产物、实时进度可视化、错误展示、产物缩略图墙、原始 SSE 日志查看
- 表单字段：原文、平台、风格、输出格式、水印；**所有下拉选择均显式提供 "自动" 选项**
- 节点状态可视化：覆盖 Agent 1 / Agent 2 / Agent 3 / Worker A~D / Gate 1 / Tool 1 / Gate 2 共 10 个节点
- 错误显示：红色横幅 + 简短文案；开发者模式下展开看 stack
- 演示/开发者模式开关

P0 **不做**（留给 P1+）：

- 多源（multi）输入 UI、视频产物播放、历史任务列表、任务对比、登录鉴权、移动端适配、节点级指标可视化（耗时/token 等仅在开发者模式下原始展示，不画图表）

### 1.3 用户路径

```
打开 /console
  → 粘贴原文 / 选择平台/风格/格式（默认全 "自动"）
  → 点击 "开始生成"
  → 表单冻结，中栏 10 个节点依次点亮
  → 收到 artifact 事件 → 右栏出图
  → 收到 done 事件 → 表单解锁，可继续下一次
  ｜
  ↓（任意阶段失败）
  → 红色横幅 + 节点变红 + 日志抽屉自动展开（开发者模式）
```

---

## 2. 接口契约

### 2.1 前端 → 后端

#### 2.1.1 创建任务（**复用现有接口**）

`POST /api/v1/generate`，请求体严格对齐 `GenerateRequest`：

```json
{
  "input": {
    "mode": "single",
    "content": "原文文本..."
  },
  "user_profile": {
    "user_type": "auto"
  },
  "preferences": {
    "target_platform": "auto",
    "output_format": "auto",
    "style_hint": "auto",
    "watermark": true,
    "qrcode": false
  },
  "trace": { "client_id": "console", "request_id": "<uuid>" }
}
```

P0 前端**永远以 `mode: "single"` 提交**；`sources` 字段不出现。

#### 2.1.2 订阅状态（**复用现有接口**）

`GET /api/v1/jobs/{job_id}/stream`，浏览器 `EventSource` 订阅，事件类型详见 §2.3。

#### 2.1.3 刷新恢复（**新增**）

`GET /api/v1/jobs/{job_id}/replay`，返回该任务**到目前为止**的全部历史事件，前端用于刷新页面后回放：

```json
{
  "job_id": "j_xxx",
  "status": "running | succeeded | failed | degraded",
  "events": [
    { "ts": "2026-04-29T10:00:01Z", "event": "stage", "data": {...} },
    { "ts": "2026-04-29T10:00:03Z", "event": "stage", "data": {...} }
  ]
}
```

实现层面：在 `_run_and_broadcast` 里把每条 `emit` 的事件同步落到 PG `stages` 表（已有），新增一行 SELECT 即可。

### 2.2 表单字段与枚举（**P0 所有下拉必须含 `auto`**）

| 字段 | 类型 | 候选值 | 默认 | UI 标签（zh-CN） |
|---|---|---|---|---|
| `input.content` | textarea | 自由文本（≥200 字校验） | 空 | 原文 |
| `preferences.target_platform` | select | `auto` / `xiaohongshu` / `wechat_moments` / `wechat_official` / `weibo` / `douyin` | `auto` | 目标平台 |
| `preferences.style_hint` | select | `auto` / `clean_business` / `xiaohongshu_warm` / `data_journalism` / `tech_minimal` / `magazine_editorial` | `auto` | 视觉风格 |
| `preferences.output_format` | select | `auto` / `image` / `video` | `auto` | 输出格式 |
| `preferences.watermark` | checkbox | `true` / `false` | `true` | 添加水印 |
| `user_profile.user_type` | select | `auto` / `professional` / `general` | `auto` | 受众类型 |

**`auto` 的行为**（与后端契约一致）：

- `target_platform=auto`：API 层 fallback 为 `xiaohongshu`（见 `routes.py: _run_and_broadcast`）
- `style_hint=auto`：交由 Worker D 根据 `article_type + user_type` 决策
- `output_format=auto`：P0 永远落到 `image`（视频是 P1）
- `user_type=auto`：交由 Agent 1 判断

**前端展示规则**：每个 `auto` 选项在下拉里显示为「自动（由系统判断）」，并在节点图相应阶段完成后，把系统实际选择的值回填到表单（只读展示，不改变提交值）。这样观众能看到"自动"背后系统选了什么。

### 2.3 SSE 事件契约（**需要后端补字段**）

P0 前端依赖以下五类事件。事件类型保持与 `routes.py` 现状一致，但 `data` 内字段需要扩展：

| event | data 字段（P0 需要） | 触发位置 |
|---|---|---|
| `stage` | `stage`(节点 ID), `status`(`running`/`done`/`failed`/`retrying`), `node_label`(中文标签), `attempt`(第几次尝试), `summary`(≤80 字摘要), `started_at`, `finished_at` | 每个 Agent/Worker/Gate/Tool 进入与离开 |
| `decision` | `node`, `field`, `value`（如 `auto` → `xiaohongshu`） | Agent 1 / Worker D 等做出"自动选择"时 |
| `error` | `message`, `node`, `recoverable`(bool), `stack`(开发者模式) | 任意节点抛错 |
| `artifact` | `render_id`, `type`, `url`, `thumbnail_url`, `width`, `height`, `platform`, `template_id` | Tool 1 / Tool 2 完成 |
| `done` | `job_id`, `status`, `degrade_level` | 流水线终止 |

**新增字段**与现状的差异（落到 `orchestrator/graph.py`）：

```python
# 现状
await emit("stage", {"stage": "agent1_router", "status": "running"})

# P0 目标
await emit("stage", {
    "stage": "agent1_router",
    "node_label": "Agent 1 · 路由",
    "status": "running",
    "attempt": 1,
    "started_at": "2026-04-29T10:00:00Z"
})
```

约定 `node_label` 在后端写死中文，避免前端维护一份 ID→标签映射表。

### 2.4 节点 ID 清单

P0 前端按以下顺序与 ID 渲染节点图（与 LangGraph StateGraph 节点名一致）：

```
agent1_router
agent2_understanding
agent3_orchestrate
worker_a_copy
worker_b_factcheck
worker_c_visual
worker_d_style
gate1_blueprint
tool1_image_render
gate2_render
```

回退（`fact_drift / blueprint_level / render_only`）在前端表现为：相关节点状态从 `passed` 退回 `retrying`，并在卡片角标上显示 `attempt=N`。

---

## 3. 前端设计

### 3.1 布局

```
┌─────────────────────────────────────────────────────────────────┐
│ 顶栏：[Logo] 长文 → 信息图 控制台      [演示 / 开发者] 切换  │
├──────────────┬──────────────────────────────┬──────────────────┤
│ 左栏 输入     │ 中栏 流水线（10 节点）        │ 右栏 产物         │
│ (24%)        │ (44%)                         │ (32%)            │
│              │                               │                  │
│ ▢ 原文文本    │ ● Agent 1 · 路由   ✓ 1.2s    │ Blueprint 摘要   │
│ (textarea)   │ ● Agent 2 · 理解   ⟳         │  · 卡片数: 6     │
│              │ ○ Agent 3 · 编排              │  · 风格: warm    │
│ 平台: [自动▾]│ ○ Worker A · 文案             │                  │
│ 风格: [自动▾]│ ○ Worker B · 校验             │ 产物缩略图墙     │
│ 格式: [自动▾]│ ○ Worker C · 视觉             │ ┌────┐ ┌────┐    │
│ 受众: [自动▾]│ ○ Worker D · 风格             │ │    │ │    │    │
│ ☑ 水印       │ ○ Gate 1 · 蓝图评估           │ └────┘ └────┘    │
│              │ ○ Tool 1 · 渲染               │                  │
│ [开始生成]    │ ○ Gate 2 · 渲染评估           │                  │
│              │                               │                  │
├──────────────┴──────────────────────────────┴──────────────────┤
│ 日志抽屉（默认收起，开发者模式自动展开）                          │
│ [stage] 10:00:01 agent1_router → running                         │
│ [stage] 10:00:02 agent1_router → done   {article_type:"policy"} │
│ ...                                                              │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 节点卡片状态机

| 状态 | 颜色 | 触发 | 说明 |
|---|---|---|---|
| `idle` | 灰 | 初始 | 任务尚未到达此节点 |
| `running` | 蓝（脉冲动画） | `stage.status=running` | 正在执行 |
| `passed` | 绿 | `stage.status=done` | 成功完成 |
| `retrying` | 橙 | `attempt>1` 且 `status=running` | 回退后重跑 |
| `degraded` | 黄 | `done` 事件 `degrade_level!=null` 时回标 | 降级通过 |
| `failed` | 红 | `error` 事件命中本节点 | 不可恢复失败 |

每个节点卡片可点击展开（accordion），展开后显示：

- 演示模式：`summary` 字段（≤80 字中文摘要）
- 开发者模式：`summary` + 历次 attempt 时间线 + 该节点累计 token / 耗时 + 触发的 `decision` 事件

### 3.3 错误展示

- 顶部红色横幅：固定占位，包含 `message` 与"展开详情"按钮
- 节点图内对应节点变红，点击可滚动到该节点
- 演示模式：横幅文案为人话（"系统正在尝试重新生成…"或"任务失败，请重试"）
- 开发者模式：横幅展开后显示 `stack` + 该节点最后一次的 `summary` + 关联日志条目高亮

P0 不做 toast 队列，只展示**最近一次**错误。

### 3.4 演示态 / 开发者态切换

| 模块 | 演示态 | 开发者态 |
|---|---|---|
| 节点卡片 | 只显示 label + 状态 | 加显 attempt / 耗时 / token |
| 节点展开 | summary 文案 | summary + 原始 JSON |
| 错误横幅 | 友好文案 | 友好文案 + stack |
| 日志抽屉 | 隐藏 | 默认展开，可过滤事件类型 |
| `auto` 回填 | 动画式高亮 | 直接展示 |
| 顶部 telemetry 条 | 不显示 | 显示 `model_calls / total_duration / retries` |

切换通过顶栏开关，状态存于 `localStorage.console_mode`（`"demo"` / `"dev"`）。

### 3.5 状态管理

P0 不引入框架，使用一个全局 `state` 对象 + `applyEvent(state, event)` 纯函数：

```js
const state = {
  jobId: null,
  formLocked: false,
  mode: "demo",                 // demo | dev
  nodes: {                      // nodeId → { status, attempt, summary, startedAt, finishedAt }
    agent1_router: { status: "idle" },
    agent2_understanding: { status: "idle" },
    // ...
  },
  decisions: [],                // [{ node, field, value }]
  artifacts: [],
  errors: [],                   // 最多保留最后 1 条
  logs: []                      // 全量原始事件，开发者模式渲染
};

function applyEvent(state, ev) { /* switch on ev.event, 纯函数返回新 state */ }
```

`EventSource.onmessage` → `applyEvent` → 触发 DOM 更新（最小化 re-render：每个节点卡片只在自己 state 变时重绘）。

### 3.6 文件结构

```
api/
  static/
    console.html        # 单文件，含 <style> <script>
  routes.py             # 新增 GET /console + GET /api/v1/jobs/{id}/replay
```

`routes.py` 增量：

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app.mount("/static", StaticFiles(directory="api/static"), name="static")

@app.get("/console")
async def console():
    return FileResponse("api/static/console.html")
```

---

## 4. 后端增量改动

P0 后端改动**严格限定**在以下三处，**不动**任何 Agent / Worker / Gate / Tool 内部逻辑：

### 4.1 SSE 事件字段扩展

位置：`orchestrator/graph.py`（每个节点 wrapper）+ `api/routes.py: _run_and_broadcast`

改动：在每次 `emit("stage", ...)` 时附加 `node_label / attempt / started_at / finished_at / summary` 字段。`summary` 由各节点返回（P0 可先用空字符串，后续逐节点补充）。

### 4.2 历史事件落库

位置：`infra/db.py` + `api/routes.py: _run_and_broadcast`

改动：在 `emit` 内除推 SSE 队列外，同步写入 PG `stages` 表（已有表结构可复用，新增字段 `event_type / event_data_json`）。失败容忍：DB 写失败仅 `logger.warning`，不影响主流程（与现有 `db_create_job_skipped` 风格一致）。

### 4.3 Replay 接口

位置：`api/routes.py`

```python
@app.get("/api/v1/jobs/{job_id}/replay")
async def replay_job(job_id: str):
    if _deps is None:
        raise HTTPException(503, "服务未就绪")
    job = await _deps.db.get_job(job_id)
    if not job:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    events = await _deps.db.list_stage_events(job_id)  # 新增方法
    return {"job_id": job_id, "status": job["status"], "events": events}
```

---

## 5. 错误与边界

| 场景 | 行为 |
|---|---|
| 用户提交时正文 < 200 字 | 前端拦截，提示"原文过短"，不发请求 |
| 用户在生成中再次点击"开始生成" | 按钮置灰 + tooltip"任务进行中"，由 `state.formLocked` 控制 |
| SSE 断线（网络/服务重启） | `EventSource` 自动重连；重连后调一次 `replay`，把丢失事件补回 |
| 任务 DB 不可用（现状已容忍） | 控制台正常工作，但刷新后无法 replay；UI 顶部显示一行黄色"持久化未启用，刷新将丢失进度" |
| Job 进入 `degraded`（降级出图） | 节点全绿但顶栏显示黄色降级提示；产物正常展示 |
| 用户在 `auto` 模式下想知道系统选了什么 | 通过 `decision` 事件回填到表单的"实际选择"标签（只读） |
| 浏览器关闭再打开 | 通过 URL hash `#job=j_xxx` 复原；进入页面时若有 hash 自动调 replay |

---

## 6. 验收清单（P0）

功能性（必须）：

- 粘贴原文 → 全 `auto` 默认值 → 一键生成图片，全程进度可见
- 10 个节点状态实时点亮，颜色与 §3.2 一致
- 任意节点失败时，红色横幅 + 节点变红 + 不卡死前端
- 产物（图片）可在右栏看到缩略图，点击放大
- 演示 / 开发者模式切换有效
- 表单的所有下拉都含 `auto` 选项且默认选中

非功能（必须）：

- 单文件 HTML 体积 ≤ 80 KB（去掉 Tailwind CDN）
- SSE 事件到 UI 渲染延迟 ≤ 200ms（局域网）
- 刷新页面后能恢复正在进行的任务（依赖 replay）

不在 P0 验收范围（P1 再看）：

- 视频产物播放
- 多源输入 UI
- 历史任务列表 / 对比视图
- 移动端适配
- 鉴权与多租户

---

## 7. 里程碑

| 阶段 | 工时（人日） | 交付物 |
|---|---|---|
| M1 后端 SSE 字段扩展 + 落库 | 1 | `node_label / attempt / summary` 上线，`stages` 表写入 |
| M2 `/api/v1/jobs/{id}/replay` 接口 | 0.5 | 单元测试 + curl 可验 |
| M3 `/console` 静态托管 + HTML 骨架 | 0.5 | 三栏布局空壳，能加载 |
| M4 表单 + POST `/generate` 联通 | 0.5 | 提交后能拿到 `job_id` |
| M5 节点图 + SSE 订阅 + 状态机 | 1.5 | 10 节点点亮、错误横幅、演示/开发者切换 |
| M6 产物缩略图墙 + 日志抽屉 + replay 恢复 | 1 | 通过 §6 验收清单 |

合计 ~5 人日，串行可一周内交付。

---

## 8. 与主文档的对齐说明

- 节点 ID 与 `tech_design.md §1.1 系统总览` 的 mermaid 图节点对齐
- 表单枚举与 `ir/models.py: GeneratePreferences / UserProfile` 的 Literal 完全一致，不引入新值
- SSE 事件类型与 `tech_design.md §2.1.2` 的事件序列示例兼容（`stage / artifact / done / error`），仅扩展 `data` 字段，不改事件名
- 后端改动严格限定在 §4 三处，不触碰 Agent / Worker / Gate / Tool 内部
