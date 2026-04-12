# 账单自动识别与分类工具 | Bill Auto-Classifier

> 读取银行账单原始数据，用 AI 自动补全交易分类和消费方式，输出结构化 CSV 供后续分析。
>
> Read raw bank transaction data, auto-classify transaction types and spending categories via AI, output structured CSV for analysis.

## 背景 | Background

银行导出的账单只有金额和商户名，缺少"这笔钱花在哪里"的分类标签。手动打标既耗时又主观。本工具通过 AI 批量补全分类，输出标准化 CSV，再结合 Excel 做消费结构分析。

Bank statements only contain amounts and merchant names — no spending category labels. Manual tagging is slow and inconsistent. This tool uses AI to batch-classify transactions and output a structured CSV ready for analysis.

## 功能 | Features

- 读取银行导出的制表符分隔账单文件
- AI 自动补全两个维度：
  - **交易分类**：消费 / 退款 / 内部转账 / 利息收入等
  - **交易方式**：外出吃饭 / 房租 / 打车 / 旅游 / 理财收入等（18 个分类）
- 批量处理，每批 50 条，支持断点续传
- 输出 UTF-8 CSV，可直接用 Excel 打开分析

## 使用方法 | Usage

**1. 安装依赖**
```bash
# 仅需 Python 标准库 + Gemini CLI
# 安装 Gemini CLI: https://github.com/google-gemini/gemini-cli
```

**2. 准备输入文件**

将银行账单导出为制表符分隔的 txt 文件，命名为 `target_bill_test.txt`，放在脚本同目录。

**3. 运行**
```bash
python process_bills.py
```

输出文件：`processed_bills_final.csv`

## 分类体系 | Category Schema

| 字段 | 选项 |
|------|------|
| 交易分类 | 消费、消费-后退款、退款、内部转账、利息收入 |
| 交易方式 | 外出吃饭、食材调料、房租、打车、旅游、回家、礼物、家居用品、演出、保险、份子钱、体检、水电煤气、在线订阅费、理财收入、送人等 |

## 扩展 | Customization

修改脚本顶部 `SYSTEM_PROMPT` 中的选项范围，即可适配自己的分类体系。
