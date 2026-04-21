# 账单梳理与财务分析 | Finance Bill Analyzer

> 用 Claude Code 对全年多银行账单做深度梳理：清洗数据、识别退款/转账、补全分类、生成交互式财务报告。

---

## 项目结构

```
bill-analyzer/
├── SKILL.md                      # Claude Code Skill 主文件
├── process_bills.py              # 早期版本：AI 批量分类脚本（Gemini CLI）
├── reference/
│   ├── classification_rules.md   # 通用分类规则参考
│   ├── refund_patterns.md        # 退款配对识别模式
│   └── transfer_patterns.md      # 内部转账识别模式
└── examples/
    └── sample_input.xlsx         # 脱敏示例数据（待添加）
```

---

## 方式一：Claude Code Skill（推荐）

`SKILL.md` 是本项目的核心，定义了一套 5 阶段的账单分析工作流，供 Claude Code 调用。

### 使用方法

**1. 把 Skill 注册到 Claude Code**

将 `SKILL.md` 复制到你的 Claude Code skills 目录：

```bash
# 全局可用
cp SKILL.md ~/.claude/skills/bill-analyzer.md

# 或仅在当前项目使用
cp SKILL.md .claude/skills/bill-analyzer.md
```

**2. 启动 Claude Code，触发 Skill**

```
你：我有一份 2025 年的银行账单 Excel，想做全年的财务复盘
Claude：[自动加载 bill-analyzer Skill，引导你拖入文件]
```

或者直接说：
```
你：/bill-analyzer 开始账单分析
```

**3. 拖入 Excel 文件**

把账单 Excel 拖进终端窗口，Claude Code 会自动读取路径，开始阶段 1。

### 工作流（5 个阶段，每阶段确认后再继续）

| 阶段 | 内容 | 输出 |
|------|------|------|
| 1 | 数据探查 + 规律预扫 | `01_overview.md` |
| 2 | 退款/转账识别与抹平 | `02_cleaned.csv` |
| 3 | 行为规律驱动的分类补全 | `03_classified.csv` |
| 4 | 分析框架确认 | — |
| 5 | 交互式 HTML 报告 + 可复现脚本 | `report.html` |

### Excel 格式要求

主表包含以下字段（字段名允许有差异，Claude 会自动识别）：

- 交易时间、交易账户、备注/商户名
- 金额（支持多币种，自动换算 CNY）
- 收/支标识、交易状态

额外建议：在 Excel 中单独建一个 sheet 放你的**二级分类体系**，Claude 会以此为准做分类。

---

## 方式二：直接使用启动 Prompt

如果不想安装 Skill，可以把下面的 Prompt 直接粘贴进 Claude Code 对话框：

<details>
<summary>展开启动 Prompt</summary>

```markdown
# 项目：银行账单梳理与财务分析

## 任务总览
帮我处理一份多银行账单 Excel：清洗数据、抹平退款/转账/还款、
补全分类、做财务分析、输出交互式 HTML 报告 + 可复现 Python 脚本。

## 文件与目录
- 我会把 Excel 拖进终端给你路径
- 读到路径后，先和我确认工作目录（默认在文件同级建 finance-YYYY/）
- 原始文件只读，不要修改

## 分阶段执行（每阶段停下等我确认）

### 阶段 1：数据探查 + 规律预扫
- 读取所有 sheet，找到分类体系 sheet
- 预扫行为规律（时间段、星期、固定金额、商户聚类等）
- 输出 processed/01_overview.md
- 停下等我确认

### 阶段 2：清洗与抹平（按"事件簇"汇报）
- 识别退款/内部转账/信用卡还款
- 每个事件簇：时间+账户+完整原始备注+配对逻辑+置信度
- 输出 02_cleaned.csv + 02_cleaning_report.md
- 停下等我确认

### 阶段 3：分类补全（按"规律簇"汇报）
- 先找规律再批量分类，不要逐笔判断
- 每个规律簇：样本+识别依据+推测分类+置信度+建议操作
- 输出 03_classified.csv + 03_ambiguous.md
- 停下等我确认

### 阶段 4：分析框架确认
- 列出指标、健康度维度、预算推导逻辑、报告结构
- 停下等我确认

### 阶段 5：最终交付
- output/report.html（单文件、含交互图表）
- scripts/ 下可复现脚本

## 核心原则
- 每阶段停下等我确认
- 不分析：内部转账、信用卡还款、已退款订单
- 原始内容要完整贴出
- 先找规律再分类

现在请等我拖入 Excel 文件。
```

</details>

---

## 方式三：早期版本脚本（process_bills.py）

早期版本，使用 Gemini CLI 对账单做批量 AI 分类，适合只需要补全分类标签而不需要完整分析流程的场景。

详见脚本顶部注释。

---

## 设计理念

### 为什么用行为规律而不是商户名分类？

商户名往往无法区分消费场景——同一家便利店，早上 8 点买的是早餐，半夜 12 点买的可能是宵夜；同一个打车 App，工作日早晨是通勤，周末深夜是聚会散场。

本 Skill 在阶段 1 就预扫行为规律（时间段 + 星期 + 金额分布 + 商户聚类），在阶段 3 用这些规律驱动分类，准确率显著高于纯商户名匹配。

### 为什么分 5 个阶段而不是一次性跑完？

账单数据有大量需要人工判断的情况：
- 退款配对：金额差了 15 元，是运费险还是部分退？
- 转账识别：这笔打给朋友的钱，是借款还是 AA 聚餐？
- 分类模糊：周末买菜算"日常采买"还是"囤货"？

全自动运行会积累错误，最终报告不可信。分阶段确认确保每一步都在用户的掌控下进行。
