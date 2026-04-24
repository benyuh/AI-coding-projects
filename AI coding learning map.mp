# AI 编程能力准备指南

## 目标

### 1. 用 AI 把想法变成原型（最核心）

给定一个产品需求，用 Claude / Cursor / Copilot 现场撸出一个能跑的 demo。

- **重点考察**：需求拆解能力 + 提示词工程能力 + 调试能力
- **不考察**：手写算法、代码风格优雅与否

### 2. 看懂技术方案，并提出产品侧的优化建议

给一段 Agent 代码（比如一个 ReAct 循环），分析如何改进。

- **重点考察**：对技术的理解深度 + 产品视角的改进思路

### 3. 用代码快速做数据分析

给一个 CSV（用户行为数据），现场分析。

- **重点考察**：Python 数据处理基本功 + 提问能力

### 4. 对 Agent / LLM 生态的实战理解

- **重点考察**：技术选型思路 + 实战踩坑经验

---

## 一、环境 + Python 基本功

**工具准备**

- Cursor（或 VS Code + Copilot）——主力编码环境
- Python 3.10+ + 必备包：`requests`、`pandas`、`openai` / `anthropic`
- API Key：至少备好 OpenAI 或 Anthropic 的 API Key

**上手步骤**

1. 安装 Cursor，熟悉 `Cmd+K`（内联编辑）、`Cmd+L`（聊天）、`Tab`（补全）
2. 新建一个 Python 项目，用 Cursor 写一个"调 Claude API 返回结果"的 hello world
3. 跑通之后，用 Cursor 重构这段代码（比如加 error handling、加 retry 机制），感受 AI 辅助编程的节奏

---

### 模块 1：数据处理（pandas）

```python
import pandas as pd

# 读 CSV
df = pd.read_csv('data.csv')

# 过滤
df_filtered = df[df['age'] > 18]

# 分组统计
df.groupby('category')['price'].mean()

# 排序
df.sort_values('revenue', ascending=False).head(10)

# 简单可视化
df['revenue'].plot()
```

> 建议练习：找一个公开数据集（Kaggle 随便下一个），做 5 个问题的分析——比如"哪个类别销量最高"、"用户年龄分布"等。

---

### 模块 2：API 调用 + JSON 处理

这是 AI Agent 开发的核心基本功：

```python
import requests
import json

# 调用 API
response = requests.post(
    'https://api.anthropic.com/v1/messages',
    headers={'x-api-key': 'YOUR_KEY'},
    json={
        'model': 'claude-3-5-sonnet-20241022',
        'max_tokens': 1024,
        'messages': [{'role': 'user', 'content': 'Hello'}]
    }
)

# 解析 JSON
data = response.json()
print(data['content'][0]['text'])
```

---

### 模块 3：基础字符串 / 文件操作

读写文件、字符串处理、正则基础。不需要多深，但要能不假思索写出来。

---

## 二、Agent 实战 + 框架熟悉

### 手搓一个 ReAct Agent

目标：用 100–200 行代码，写一个能查天气的 ReAct Agent。

---

### 扩展项目方向（选一个做深）

#### 扩展 A：加入 Tool Use，做成真 Agent（最推荐）

- 现有单轮推理模式不够灵活——用户需求表达不完整时，Agent 应该主动追问
- 扩展成 ReAct Agent：让 LLM 自己决定"我要查哪个品类的商品、要不要继续追问用户"
- 工具层面包含 `query_products`、`ask_clarification`、`get_user_preference` 三个 action
- 代码量约 200 行，2–3 小时能搞定

#### 扩展 B：加入简单的 RAG

- 把已有的 JSON 商品库做成 embedding
- 用户问"敏感肌防晒" → 向量检索 → 召回相关商品 → 丢给 LLM
- 使用 `sentence-transformers` + `faiss`（或更简单的 cosine similarity）
- 商品池扩大后 context 装不下时，加一层向量召回：先 Top 20 再让 LLM 精选
- 代码量约 150 行

#### 扩展 C：加评估脚本

- 写一个 `eval.py`：给定一批测试 query，跑推荐，输出 LLM Judge 的打分
- 用 GPT-4 做 Judge，按准确性、相关性、分寸感三个维度打分
- 跑完能看到平均分和 bad case 列表

> 建议：选**扩展 A + C**，两个都不大，加起来 4–6 小时能搞定。Agent 化让项目更完整，评估脚本完整覆盖评估体系搭建。

---

## 三、编码练习

限定 30 分钟完成以下任务，每个做一遍：

### 任务 1：数据分析题

给定一个 CSV（用户与 Agent 的对话日志），分析：

1. 哪些品类的对话转化率最高？
2. 平均对话轮数和转化率的关系？
3. 给出 3 条产品优化建议

**考察点**：pandas 熟练度 + 分析思路

---

### 任务 2：Agent 改造题

给定一个只支持单轮对话的简单 Agent，改造成支持多轮对话、保留上下文。

**考察点**：对 messages 结构的理解 + 工程能力

---

### 任务 3：Prompt 调优题

给定一个让 Agent 推荐商品的 prompt，但经常出现幻觉，进行改造。

**考察点**：对 LLM 约束的理解 + Prompt 设计能力

---

**练习方法**

- 严格计时，模拟压力场景
- 全程用 Cursor，把"提示 AI → 审查代码 → 调试"的流程跑顺
- 不追求一次写对，追求"出问题能改"
