"""
v3.1.3 — main.py
长文 → 信息图 PNG，Gate 1 + Gate 2 质量闭环版本。

架构：Agent1 → Agent2 → Agent3(Worker A/B/C/D) → Gate1 → Tool1 → Gate2 → PNG
（通过 LangGraph StateGraph 串联，带重试预算和降级机制）

用法：
    python3.11 main.py                          # 使用内置测试长文
    python3.11 main.py --file path/to/text.txt  # 从文件读取
    python3.11 main.py --text "你的长文内容"     # 直接传入文字
    python3.11 main.py --output my_output.png    # 指定输出路径
    python3.11 main.py --save-blueprint          # 同时保存 Blueprint JSON
    python3.11 main.py --no-gates                # 跳过 Gate（兼容 v3.1.2 模式）
"""

import argparse
import json
import pathlib
import datetime
import sys
import time

from infra.tracing import trace
from ir.models import OutputFormat, RiskLevel, Source, SourceAuthority, SourceBundle, SourceMode
from agents.agent0_multisource import run_agent0_multisource
from agents.agent1_router import run_agent1_router
from agents.agent2_understand import run_agent2_understand
from agents.agent3_orchestrate import run_agent3_orchestrate
from tools.tool1_image_render import run_tool1_render
from orchestrator.graph import run_pipeline_with_gates

_HERE = pathlib.Path(__file__).parent


# ── 主 Pipeline ────────────────────────────────────────────────────────────

@trace("Pipeline.Main")
def run_pipeline(
    text: str | None,
    output_path: str,
    save_blueprint: bool = False,
    use_gates: bool = True,
    source_bundle: SourceBundle | None = None,
    output_format: OutputFormat | str = OutputFormat.IMAGE,
) -> str:
    """
    端到端 Pipeline：长文/多信源 → PNG/MP4。
    返回输出文件路径。

    use_gates=True（默认）：走 Gate 1/2 质量闭环（v3.1.3）
    use_gates=False：跳过 Gate，直接渲染（v3.1.2 兼容模式）
    """
    t_total_start = time.time()

    if isinstance(output_format, str):
        output_format = OutputFormat(output_format)
    source_bundle = source_bundle or SourceBundle(text=text or "")
    source_bundle = run_agent0_multisource(source_bundle)
    text = source_bundle.text

    # Gate 模式：委托给 LangGraph 图（含重试预算和降级）
    if use_gates:
        print(f"\n[Pipeline] 输入: {source_bundle.char_count} 字符 | {source_bundle.mode.value} | {output_format.value} | Gate 质量闭环模式（v3.1.3）")
        result_path, final_state = run_pipeline_with_gates(text, output_path, source_bundle=source_bundle, output_format=output_format)

        # 可选保存 Blueprint
        if save_blueprint and final_state.get("blueprint"):
            bp_path = output_path.replace(".png", "_blueprint.json")
            with open(bp_path, "w", encoding="utf-8") as f:
                json.dump(
                    final_state["blueprint"].model_dump(),
                    f, ensure_ascii=False, indent=2, default=str
                )
            print(f"[Pipeline] Blueprint 已保存: {bp_path}")

        t_total = time.time() - t_total_start
        deg = final_state.get("degradation_level", "")
        deg_note = f" | 降级: {deg}" if deg else ""
        artifact = final_state.get("render_artifact")
        size_kb = artifact.file_size_kb if artifact else 0
        cards = len(final_state["blueprint"].cards) if final_state.get("blueprint") else 0
        print(
            f"\n[Pipeline] ✅ 端到端完成: {t_total:.1f}s | "
            f"输出: {result_path} ({size_kb} KB) | "
            f"{cards} 张卡片{deg_note}"
        )
        return result_path

    # --no-gates 模式：v3.1.2 直连模式
    print(f"\n[Pipeline] 输入: {source_bundle.char_count} 字符 | {source_bundle.mode.value} | {output_format.value} | 直连模式（无 Gate，v3.1.2 兼容）")

    router_decision = run_agent1_router(source_bundle)
    if output_format != OutputFormat.AUTO:
        router_decision = router_decision.model_copy(update={"output_format_hint": output_format})
    if router_decision.risk_level == RiskLevel.BLOCKED and router_decision.skip_reason == "TEXT_TOO_SHORT":
        raise ValueError(f"文章过短（{source_bundle.char_count} 字），无法生成信息图")
    if router_decision.risk_level == RiskLevel.BLOCKED and router_decision.skip_reason != "TEXT_TOO_SHORT":
        raise ValueError(f"内容被拒绝: {router_decision.risk_reason}（类型：法律/医疗/诗歌/PII 等高风险内容）")

    content_tree = run_agent2_understand(source_bundle, router_decision)
    blueprint = run_agent3_orchestrate(source_bundle, router_decision, content_tree)
    chosen_format = output_format if output_format != OutputFormat.AUTO else router_decision.output_format_hint
    if chosen_format == OutputFormat.AUTO:
        chosen_format = OutputFormat.IMAGE
    blueprint = blueprint.model_copy(update={"output_format": chosen_format})

    if save_blueprint:
        bp_path = output_path.replace(".png", "_blueprint.json")
        with open(bp_path, "w", encoding="utf-8") as f:
            json.dump(blueprint.model_dump(), f, ensure_ascii=False, indent=2, default=str)
        print(f"[Pipeline] Blueprint 已保存: {bp_path}")

    if blueprint.output_format == OutputFormat.VIDEO:
        from tools.tool2_video_render import run_tool2_video_render
        try:
            artifact = run_tool2_video_render(blueprint, output_path)
        except Exception as e:
            print(f"[Pipeline] 视频生成失败，降级输出图片: {e}")
            artifact = run_tool1_render(blueprint.model_copy(update={"output_format": OutputFormat.IMAGE}), output_path)
    else:
        artifact = run_tool1_render(blueprint, output_path)
    t_total = time.time() - t_total_start
    print(
        f"\n[Pipeline] ✅ 端到端完成: {t_total:.1f}s | "
        f"输出: {artifact.output_path} ({artifact.file_size_kb} KB) | "
        f"{len(blueprint.cards)} 张卡片"
    )
    return artifact.output_path


# ── 内置测试文本 ──────────────────────────────────────────────────────────

_BUILTIN_TEST_TEXT = """
# 2024年中国AI大模型行业全景报告

## 一、产业规模与增速

根据中国信通院发布的《人工智能发展报告2024》，2024年中国AI大模型市场规模已突破1200亿元，同比增长达到186%，成为全球增速最快的单一AI细分赛道。

截至2024年底，国内已备案的大模型数量超过180个，实际对外提供服务的模型约120个，涵盖通用大模型、垂直行业大模型两大类别。中国已成为全球备案大模型数量最多的国家，超越美国位居第一。

从参数规模来看，国内头部模型（文心一言5.0、通义千问2.5、混元Large等）参数量普遍达到5000亿至万亿级别，与OpenAI GPT-4o处于同一数量级，在中文理解、数学推理、代码生成三项能力上已基本持平甚至小幅领先。

## 二、核心玩家格局

**BAT系（互联网巨头）**
- 百度文心一言：日活用户突破1.2亿，企业API调用量占市场份额约38%，是目前国内商业化最成熟的大模型平台
- 阿里通义千问：开源版本Qwen2.5系列在全球Huggingface下载量超10亿次，是国内下载量最大的开源模型
- 腾讯混元：深度整合微信生态，月活跃调用场景超5000个，内容创作场景渗透率最高

**创业独角兽**
- 智谱AI（ChatGLM）：完成C轮融资25亿元，估值超200亿，高校和科研机构用户占比最高
- 月之暗面（Kimi）：长上下文能力（支持200万字）是核心差异化，融资累计超60亿元
- MiniMax：多模态能力突出，已打入海外市场，月均海外用户增速超40%
- 零一万物（Yi系列）：李开复创立，Yi-Large在国际权威榜单MMLU中位列中文模型第一

**科技巨头新势力**
- 华为盘古：专注工业、政府、金融垂直领域，已与超3000家企业签署合作
- 字节跳动豆包：MAU（月活跃用户）已突破6000万，是国内用户增速最快的消费级AI应用

## 三、技术路线分化

2024年，国内大模型技术路线出现明显分化：

**多模态整合**成为共识。文生图、图生文、视频理解、语音交互被各家视为标配，而非差异化竞争点。Sora类视频生成虽仍处早期，但国内快手可灵、字节即梦已实现商业化落地。

**长上下文竞争**白热化。从最初的4K上下文，到现在月之暗面的200万token，这一指标的军备竞赛正在重塑搜索和文档处理行业格局。

**推理能力专项突破**是2024年下半年最热的技术话题。受OpenAI o1模型启发，国内各家纷纷推出强化版推理模型：百度ERNIE-Speed-Reasoning、阿里Qwen-Reasoning、深度求索DeepSeek-R1均展现出接近o1的数学和逻辑推理能力，其中DeepSeek-R1在国际榜单上以大幅低于GPT-4的训练成本取得了可比性能，引发全球关注。

**AI Agent（智能体）**从概念走向落地。2024年，超过60%的企业AI采购已转向Agent产品，而非单纯的大模型API调用。主要落地场景包括：客服自动化（ROI显著）、代码辅助（Copilot类工具）、数据分析（Text-to-SQL）、内容生产（营销物料批量生成）。

## 四、商业化挑战

尽管规模快速增长，行业整体仍面临三大核心挑战：

**盈利压力**：仅有约15%的大模型企业实现盈利，其余85%仍处于亏损状态。算力成本居高不下（一次完整的预训练成本超过5000万美元），价格战又导致API单价持续下探，部分基础模型API价格已低于边际成本。

**数据合规**：《生成式人工智能服务管理暂行办法》实施后，训练数据的版权确权、个人信息保护成为合规重点。2024年已有3家企业因数据合规问题收到监管函。

**落地深度有限**：企业级部署中，AI产出内容的准确率不足已是主要卡点。在金融、医疗等高精度要求场景中，幻觉（Hallucination）问题导致实际落地率不足预期的40%。

## 五、2025年展望

业界主流预测：
- 中国大模型市场规模将在2025年突破3000亿元
- 参数规模竞争趋于理性，小而精的垂直模型（10B-70B参数）将占据更多市场份额
- AI芯片国产替代提速，华为昇腾910C、寒武纪MLU系列将在2025年分担约20%的训练算力需求
- 多智能体协作（Multi-Agent）将成为企业AI基础设施的新标配

**关键结论**：中国大模型已从"追赶期"进入"并跑期"，部分细分能力实现领跑。但商业化深度和生态成熟度与美国仍有2-3年差距，规模化盈利是下一个最关键的里程碑。
"""


# ── CLI 入口 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="v3.1.2 — 长文转信息图 PNG（完整 Pipeline 骨架，无 Gate）"
    )
    parser.add_argument("--text", type=str, default=None, help="直接传入长文文本")
    parser.add_argument("--file", type=str, default=None, help="从文件读取长文")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出 PNG 路径（默认：output_YYYYMMDD_HHMMSS.png）",
    )
    parser.add_argument(
        "--save-blueprint",
        action="store_true",
        default=False,
        help="同时保存 Blueprint JSON 文件",
    )
    parser.add_argument(
        "--no-gates",
        action="store_true",
        default=False,
        help="跳过 Gate 1/2 质量检查（v3.1.2 兼容模式，速度更快）",
    )
    parser.add_argument(
        "--output-format",
        choices=["image", "video", "auto"],
        default="image",
        help="输出格式：image/png 或 video/mp4（默认 image）",
    )
    args = parser.parse_args()

    # 确定输入文本
    if args.file:
        text = pathlib.Path(args.file).read_text(encoding="utf-8")
        print(f"[读取] 从文件加载：{args.file}（{len(text)} 字符）")
    elif args.text:
        text = args.text
        print(f"[读取] 使用命令行文本（{len(text)} 字符）")
    else:
        text = _BUILTIN_TEST_TEXT
        print(f"[读取] 使用内置测试长文（{len(text)} 字符）")

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(_HERE / f"output_{ts}.png")

    # 运行 Pipeline
    try:
        result = run_pipeline(
            text=text,
            output_path=output_path,
            save_blueprint=args.save_blueprint,
            use_gates=not args.no_gates,
            output_format=args.output_format,
        )
        print(f"\n✅ 成功！输出文件：{result}")
    except ValueError as e:
        print(f"\n❌ Pipeline 拒绝处理：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Pipeline 失败：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
