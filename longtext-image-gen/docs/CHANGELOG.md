# CHANGELOG

本文档按版本记录系统变更。每个版本包含：功能说明、修复的坑、测试耗时。

---

## v3.1.5 — FastAPI 服务层（2026-04-28）

在 v3.1.4 渲染层基础上，新增 HTTP 服务层，将 CLI 能力暴露为 REST API。

异步模型：主事件循环不阻塞；渲染任务通过 `ThreadPoolExecutor(max_workers=4)` 在独立线程中执行完整 Pipeline。

HTTP 端点：

- `POST /render` — 提交渲染任务（202 Accepted，返回 job_id）
- `GET /status/{job_id}` — 查询任务状态和结果
- `GET /download/{job_id}` — 下载已完成的 PNG 文件
- `GET /health` — 健康检查

### v3.1.5 新增文件

| 文件 | 功能 |
| ---- | ---- |
| `service/__init__.py` | 模块标识 |
| `service/schemas.py` | 请求/响应 Pydantic 模型（RenderRequest / JobResult / JobStatus） |
| `service/job_store.py` | 线程安全内存任务状态存储 |
| `service/app.py` | FastAPI 主应用，lifespan 管理线程池生命周期 |
| `requirements.txt` | 项目依赖文件 |
| `tests/unit/test_service_smoke.py` | 16 项服务层冒烟测试 |

### v3.1.5 关键设计决策

1. `_executor` 在 FastAPI `lifespan` 中创建和销毁，避免跨 `TestClient` 实例共享时触发 `RuntimeError`
2. `InMemoryJobStore.update()` 通过 `model_copy(update={})` 返回新 Pydantic 对象，与 RetryBudgetState 保持一致的不可变模式
3. `/download` 状态语义：任务存在但未完成 → 409；任务不存在 → 404；文件已清理 → 410

### v3.1.5 修复的坑

- `_executor` 模块级初始化会在 `TestClient` 首次 `with` 退出时被关闭，后续测试提交任务报 `RuntimeError`。修复：移入 `lifespan`，每次应用启动时重建。

### v3.1.5 测试记录

```text
tests/unit/test_smoke.py            18 passed  (v3.1.1/v3.1.2 回归)
tests/unit/test_gate_smoke.py       12 passed  (v3.1.3 Gate 回归)
tests/unit/test_v314_smoke.py       15 passed, 1 skipped  (v3.1.4 回归)
tests/unit/test_service_smoke.py    16 passed  (v3.1.5 新增)
全局合计：61 passed, 1 skipped
```

### v3.1.5 验收结果

- [x] `GET /health` 返回 `{"status": "ok", "version": "3.1.5"}`
- [x] `POST /render` 返回 202 + `job_id`（不等 Pipeline 完成）
- [x] `GET /status/{job_id}` 正确返回任务状态
- [x] 不存在的 job_id 返回 404
- [x] pending 任务下载返回 409
- [x] mock Pipeline 后任务最终状态为 `done`
- [x] 100 线程并发创建任务无竞态
- [x] `RenderRequest` 文本不足 100 字返回 422

---

## v3.1.4 — 模板多样化 + 风格系统 + PIL 水印（2026-04-28）

在 v3.1.3 质量闭环基础上，扩充渲染层能力：

- 模板系统：10 种叙事结构 × 2 平台（xiaohongshu / wechat_moments）= 20 个 Jinja2 模板
- 风格系统：新增 4 套 YAML 风格（xiaohongshu_warm / data_journalism / tech_minimal / magazine_editorial），共 5 套
- Chart.js + D3 CDN 注入：DATA_CHART / ENTITY_GRAPH 卡片自动注入对应 CDN 脚本
- PIL 水印：Playwright 截图后附加品牌水印（右下角半透明圆角框 + 多源信源顶部标注）
- 共享组件：`render/templates/shared/base_styles.html` + 8 个卡片 Macro 组件

### v3.1.4 新增文件

| 文件 | 功能 |
| ---- | ---- |
| `render/templates/shared/base_styles.html` | 所有模板共享的 CSS 变量与基础样式 |
| `render/templates/shared/components/*.html` | 8 个卡片组件 Macro |
| `render/templates/*/xiaohongshu.html` (10个) | 10 种结构小红书版模板 |
| `render/templates/*/wechat_moments.html` (10个) | 10 种结构微信朋友圈版模板（900px 宽，紧凑字体） |
| `render/styles/xiaohongshu_warm.yaml` | 珊瑚粉暖色系，border_radius: 24 |
| `render/styles/data_journalism.yaml` | 深色背景 #0D1117，绿色 accent #1DB954 |
| `render/styles/tech_minimal.yaml` | 白底蓝色 #0066FF，圆角 12px |
| `render/styles/magazine_editorial.yaml` | 奢华金 #C9A84C + 深黑，line_height: 1.8 |
| `render/watermark.py` | PIL 水印后处理，品牌标识 + 多源信源标注 |
| `tests/unit/test_v314_smoke.py` | 15 项 v3.1.4 冒烟测试 |

### v3.1.4 关键设计决策

1. CDN 注入点：替换 `</head>` 而非 `</body>`，确保 Chart.js 在 DOM 渲染前加载
2. YAML 优先级：`style_yaml.get(key, st.key)` — YAML 覆盖 StyleTokens，StyleTokens 作为兜底
3. 水印不阻断：Pillow 未安装时 `add_watermark()` 直接返回原路径，不影响主流程
4. `--data-gradient` CSS 变量从风格 YAML 注入，支持数据卡片独立渐变

### v3.1.4 修复的坑

- `CardContent.body` 为必填字段（Pydantic v2 required），测试数据补充 `body=""` 参数
- CDN 注入：无 `<head>` 标签的 legacy 模板改为插到 HTML 开头

### v3.1.4 测试记录

```text
tests/unit/test_smoke.py        18 passed  (v3.1.1/v3.1.2 回归)
tests/unit/test_gate_smoke.py   12 passed  (v3.1.3 Gate 回归)
tests/unit/test_v314_smoke.py   15 passed, 1 skipped  (v3.1.4 新增)
全局合计：45 passed, 1 skipped
```

### v3.1.4 验收结果

- [x] CDN 脚本注入位置正确（`</head>` 前）
- [x] 5 套风格 YAML 均可加载，关键颜色字段校验通过
- [x] `_blueprint_to_render_data` 输出包含 `cdn_scripts / css_vars / --data-gradient`
- [x] `add_watermark` 模块可导入，`_PIL_AVAILABLE` 类型为 `bool`
- [x] 无 Pillow 时水印降级返回原路径（非阻断）

---

## v3.1.3 — Gate 1 + Gate 2（质量闭环）（2026-04-28）

在 v3.1.2 Pipeline 基础上，接入两道质量 Gate 和 LangGraph 编排图：

```text
main.py（CLI 入口）
  → Orchestrator Graph（LangGraph StateGraph）
      → Agent1 Router
      → Agent2 内容理解
      → Agent3 Orchestrator（Workers A/B/C/D）
      → Gate 1 蓝图评估（4 维并行 LLM + 标题党阶段 2）
          → 失败时按维度回退（agent2 / agent3 / worker_a）
      → Tool1 图片渲染
      → Gate 2 渲染评估（5 项检查）
          → 失败时按 issue_type 分级回退
      → RetryBudget 控制（render_only≤3 / blueprint_level≤2 / fact_drift≤1）
      → 超限时 L1/L2/L3 降级输出
```

### v3.1.3 新增文件

| 文件 | 功能 |
| ---- | ---- |
| `ir/models.py`（扩展） | 新增 Gate1DimScore / Gate1Result / Gate2Issue / Gate2Result / RetryBudgetState |
| `gates/gate1_blueprint.py` | 4 维并行 LLM 评分 + 标题党阶段 2 LLM 批量判定 |
| `gates/gate2_render.py` | 5 项渲染检查（溢出/字号/OCR/Vision LLM/事实漂移），OCR 优雅降级 |
| `orchestrator/retry_budget.py` | 三层重试预算计数器，高层级触发归零低层级 |
| `orchestrator/graph.py` | LangGraph StateGraph 主图，11 节点，条件路由 |
| `tests/unit/test_gate_smoke.py` | 12 个 Gate 冒烟测试（全部通过） |

### v3.1.3 关键设计决策

1. Gate1 单次评分模式：每维度单次 LLM 调用，`voting_enabled=False`，为 v3.2+ 开启 Voting 预留接口
2. `_eval_faithfulness` 从 `Claim.evidence_span` 取真实原文片段，不再使用占位文本
3. `blueprint_level` 回退时，`render_only` 计数器归零，避免累计计数导致过早降级
4. `--no-gates` 参数：跳过 Gate 走 v3.1.2 直连模式，方便对比和调试
5. OCR 非静默降级：PaddleOCR 未安装时通过 `print` 输出明确警告

### v3.1.3 修复的坑

1. Pydantic v2 不可变对象：`RetryBudgetState` 计数更新使用 `model_copy(update={})` 返回新实例，而非 `object.__setattr__`
2. LangGraph 路由函数不能更新状态：预算更新在节点函数（而非路由函数）中通过返回新状态完成
3. Gate1 并行线程池：4 维评估使用 `ThreadPoolExecutor` 并行，避免串行 4 次 LLM 调用

### v3.1.3 测试记录

| 测试类型 | 结果 | 耗时 |
| ---- | ---- | ---- |
| 原有冒烟测试（18 个） | 18/18 | 0.19s |
| Gate 冒烟测试（12 个） | 12/12 | 0.52s |
| RetryBudget 三层逻辑 | 全覆盖 | — |
| Gate2 字号边界测试（18px/17px） | 通过 | — |
| OCR 降级非静默验证 | 通过 | — |
| LangGraph 图编译（11节点） | 通过 | — |

### v3.1.3 验收结果

| 验收标准 | 状态 |
| ---- | ---- |
| Gate1 语义忠实度有真实 evidence_span | ✅ |
| Gate1 日志明确标注单次评分模式 | ✅ |
| 触发 render_only 回退后自动重渲染 | ✅ |
| OCR 未安装时有明确日志，优雅降级 | ✅ |
| borderline 标题在日志中可见 | ✅ |
| 重试预算计数在日志中可见 | ✅ |

---

## v3.1.2 — 完整 Pipeline 骨架（无 Gate）（2026-04-29）

从 4 文件单 prompt 架构重构为多 Agent Pipeline：

```text
main.py（CLI 入口）
  → Agent1 Router（风险预检 + LLM 路由决策）
  → Agent2 内容理解（分块 + Claim 抽取 + 卡片预算）
  → Agent3 Orchestrator
      → Worker A 文案（并发，Semaphore≤8）
      → Worker B 事实核验（串行，纯规则）
      → Worker C 视觉结构（G1 多样性约束）
      → Worker D 风格选择（纯规则，矩阵查表）
  → Blueprint IR（Pydantic 完整模型）
  → Tool1 图片渲染（Playwright）
  → PNG
```

### v3.1.2 新增文件

| 文件 | 功能 |
| ---- | ---- |
| `ir/models.py` | 全套 Pydantic 数据契约 |
| `infra/config.py` | 统一环境变量读取 |
| `infra/tracing.py` | `@trace` 装饰器（同步/异步均支持） |
| `llm/client.py` | 统一 LLM 客户端（重试、JSON 鲁棒解析） |
| `agents/agent1_router.py` | 路由决策 + 风险预检 + TEXT_TOO_SHORT/BLOCKED 短路 |
| `agents/agent2_understand.py` | 分块 + Claim 抽取 + PRD 4.3.2 卡片预算计算 |
| `agents/agent3_orchestrate.py` | asyncio 并发编排 + Blueprint 组装 |
| `workers/worker_a_copy.py` | 文案生成（professional/general 双 prompt + 标题党阶段1） |
| `workers/worker_b_factcheck.py` | 事实核验（纯规则：正则数字+时间+实体别名字典） |
| `workers/worker_c_visual.py` | 视觉结构（G1：text_with_icon ≤60%，连续≤3） |
| `workers/worker_d_style.py` | 风格选择（纯规则：结构-风格矩阵查表 + 平台微调） |
| `tools/tool1_image_render.py` | 图片渲染（接收 Blueprint，CSS 变量注入 StyleTokens） |
| `configs/clickbait_keywords.yaml` | 标题党关键词 |
| `configs/entity_alias.yaml` | 实体别名字典 |
| `configs/platform_scaling.yaml` | 平台适配参数 |
| `tests/unit/test_smoke.py` | 18 个冒烟测试（全部通过） |

### v3.1.2 修复的坑

1. `asyncio.Semaphore` + `loop.run_in_executor`：Worker A 是同步 LLM 调用，需要用 `run_in_executor` 包装才能并发执行
2. Pydantic v2 中不再用 `.copy()`，改用 `.model_copy(update={})`
3. Jinja2 多模板目录需要用 `ChoiceLoader` 组合加载器，否则回退逻辑会报 `TemplateNotFound`

### v3.1.2 测试记录

| 测试场景 | 结果 | 端到端耗时 | 输出大小 |
| ---- | ---- | ---- | ---- |
| 内置测试（AI大模型报告，1897字，10张卡） | ✅ | 53.1s | 1229 KB |
| 法律/医疗内容拒绝测试 | ✅ BLOCKED | 3.1s | — |
| 过短文本拒绝测试 | ✅ TEXT_TOO_SHORT | <0.1s | — |
| 单元冒烟测试（18 个） | ✅ 18/18 | 0.35s | — |

### v3.1.2 验收结果

| 验收标准 | 状态 |
| ---- | ---- |
| 全流程跑通，输出 PNG | ✅ |
| RouterDecision 正确拒绝法律/医疗/诗歌 | ✅ |
| 每张卡 b_check 为 passed 或 degraded | ✅ |
| visual_type 至少 2 种（实际 4 种） | ✅ |
| 卡片数量在动态预算范围内 | ✅ |
| @trace 日志生效，节点耗时可见 | ✅ |

---

## v3.1.1 — 渲染精修 + 可观测性（2026-04-28）

### v3.1.1 render.html 视觉精修

- cover 卡：主标题字号 52px → 56px，加 `font-weight: 900`；从 2 层装饰圆升级为 3 层；增加底部橙色分割线
- data 卡：引入 `data-number-wrap` 容器；超长数字分级缩放（>5字 60px，>8字 44px）+ `word-break: break-all`
- timeline 卡：时间字段升级为渐变徽章；`timeline_time` 为空时左侧列自动隐藏
- section 卡：全字段加 `overflow-wrap: break-word`，降低长文本溢出概率
- summary 卡：列表前置标记升级为圆形背景图标；增加"AI摘要"免责声明 footer

### v3.1.1 main.py 增强

- 接入 `message.usage`（input_tokens / output_tokens）；精确计时：LLM 调用耗时、渲染耗时、总端到端耗时
- JSON 防御校验：`cards < 2` 时抛出 `ValueError`；`card_type` 不在枚举内时降级为 `section`；`title > 18 字` / `body > 80 字` 截断

### v3.1.1 修复的坑

1. 中文字段值内含未转义引号（`Expecting ',' delimiter`）：逐字符扫描检测字符串内的 `"` 并转义为 `\"`
2. 字段值内含裸换行（`Invalid control character`）：`in_string` 状态下将 `\n/\r/\t` 替换为转义版本

### v3.1.1 测试记录

| 测试场景 | 结果 | LLM 耗时 | 渲染耗时 | 输出大小 |
| ---- | ---- | ---- | ---- | ---- |
| 内置测试文本（AI大模型报告，1897字） | ✅ 10张卡片 | 24.1s | 2.0s | 1231 KB |
| 压力测试（碳中和政策，1693字） | ✅ 10张卡片 | 23.2s | 2.1s | 1256 KB |
| cards < 2 错误拒绝测试 | ✅ 友好报错 | — | — | — |
| 含裸换行 JSON 解析 | ✅ 清理后成功 | — | — | — |
| 含未转义引号 JSON 解析 | ✅ 清理后成功 | — | — | — |

### v3.1.1 验收结果

| 验收标准 | 状态 |
| ---- | ---- |
| data 卡数字大字 > section 卡，超长数字不破版 | ✅ |
| cover 有封面感，summary 有收尾感 | ✅ |
| 控制台输出 token 消耗和各阶段耗时 | ✅ |
| body > 80 字时渲染不溢出（已截断） | ✅ |
| cards < 2 时报友好错误，不产出空白图 | ✅ |

---

## v3.1.0 — Pre-P0 极简验证版（2026-04-28）

4 个文件，单 prompt + Playwright 渲染，已验证：

- 内置测试长文（中国AI大模型报告）能成功出图
- `--file` / `--text` / `--output` 参数均可用
- `output_20260428_230514.png`、`output_policy_test.png` 已生成
