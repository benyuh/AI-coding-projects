"""
tools/tool1_image_render.py — Tool 1 图片渲染（v3.1.4）

功能：
- 接收 Blueprint 而非裸 JSON
- 从 Blueprint 的 cards 组装渲染数据
- 渲染模板：优先使用 render/templates/，回退到 render.html
- CSS 变量从 StyleTokens 注入
- v3.1.4 新增：Chart.js/D3 CDN 注入、5 种风格 YAML 加载、PIL 水印后处理
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import pathlib
import tempfile
import time
from typing import Optional

import yaml
from jinja2 import Environment, FileSystemLoader, ChoiceLoader
from playwright.async_api import async_playwright

from infra.tracing import trace
from ir.models import Blueprint, CardType, RenderArtifact, VisualType
from render.watermark import add_watermark

_HERE = pathlib.Path(__file__).parent.parent
_TEMPLATES_DIR = _HERE / "render" / "templates"
_STYLES_DIR = _HERE / "render" / "styles"
_LEGACY_TEMPLATE = _HERE / "render.html"

# Chart.js + D3 CDN（离线时 Playwright 可能无法加载，但在线渲染时正常）
_CHARTJS_CDN = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
_D3_CDN = '<script src="https://cdn.jsdelivr.net/npm/d3@7.8.5/dist/d3.min.js"></script>'


def _get_template_env() -> Environment:
    """构建 Jinja2 模板环境，支持 render/templates/ 和根目录。"""
    loaders = [FileSystemLoader(str(_TEMPLATES_DIR))]
    if _LEGACY_TEMPLATE.parent.exists():
        loaders.append(FileSystemLoader(str(_HERE)))
    return Environment(
        loader=ChoiceLoader(loaders),
        autoescape=True,
    )


def _load_style_yaml(style_id: str) -> dict:
    """加载风格 YAML 文件，失败时返回空 dict（使用 CSS 变量默认值）。"""
    style_path = _STYLES_DIR / f"{style_id}.yaml"
    if style_path.exists():
        try:
            return yaml.safe_load(style_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[Tool1] 风格 YAML 加载失败 {style_id}: {e}")
    return {}


def _select_template(blueprint: Blueprint) -> str:
    """
    选择模板文件：
    优先 render/templates/{structure}/{platform}.html，
    回退到 render.html。
    """
    structure = blueprint.router_decision.narrative_structure.value
    platform = blueprint.router_decision.platform.value

    candidate = _TEMPLATES_DIR / structure / f"{platform}.html"
    if candidate.exists():
        return str(candidate.relative_to(_TEMPLATES_DIR))

    # 回退：找同结构任意平台
    fallback_dir = _TEMPLATES_DIR / structure
    if fallback_dir.exists():
        for f in fallback_dir.glob("*.html"):
            return str(f.relative_to(_TEMPLATES_DIR))

    # 兜底：直接用根目录 render.html
    return None  # 标记使用 legacy


class _CardObj:
    """简单对象，避免 Jinja2 的 dict.items() 歧义。"""
    pass


# v3.1.6 demo：清洗 Worker A 输出的占位符字面量（None/null/NULL）
# 原因：Worker A 在 LLM JSON 输出中常把可选字段写成字符串 "None"，
# 而模板对 data_label / timeline_time 直接打印 → 画面出现大字 "None"。
_PLACEHOLDER_TOKENS = {"none", "null", "n/a", "na", "undefined", "nil"}


def _clean_str(v, fallback: str = "") -> str:
    """把 None / 字符串 'None' / 'null' / 'N/A' 等占位符规范化为 fallback。"""
    if v is None:
        return fallback
    if isinstance(v, str):
        if v.strip().lower() in _PLACEHOLDER_TOKENS:
            return fallback
        return v
    return v


def _clean_items(items):
    """过滤 list 中的占位符条目（保持顺序），并对每条做字符串清洗。"""
    if not items:
        return []
    out = []
    for it in items:
        if it is None:
            continue
        if isinstance(it, str):
            cleaned = _clean_str(it)
            if cleaned:
                out.append(cleaned)
        else:
            out.append(it)
    return out


def _blueprint_to_render_data(blueprint: Blueprint) -> dict:
    """
    将 Blueprint 转换为渲染模板需要的数据字典。
    向后兼容 v3.1.1 的 render.html 格式。
    v3.1.4 新增：chart_data / graph_data / cdn_scripts
    """
    card_objs = []
    has_chart = False
    has_graph = False

    for card in blueprint.cards:
        obj = _CardObj()
        content = card.content
        visual = card.visual_spec

        # card_type：从 VisualSpec 推断
        vt_to_ct = {
            VisualType.COVER_HERO: "cover",
            VisualType.DATA_HIGHLIGHT: "data",
            VisualType.TIMELINE_VERTICAL: "timeline",
            VisualType.TEXT_WITH_ICON: "section",
        }
        obj.card_type = visual.card_type.value
        if visual.visual_type in vt_to_ct:
            obj.card_type = vt_to_ct[visual.visual_type]
        if card.card_index == 0:
            obj.card_type = "cover"
        if card.card_index == len(blueprint.cards) - 1:
            obj.card_type = "summary"

        # v3.1.6 demo：所有字符串字段过 _clean_str，过滤 "None"/"null" 占位符
        clean_title = _clean_str(content.title)
        clean_body = _clean_str(content.body)
        obj.title = clean_title[:18] if clean_title else ""
        obj.body = clean_body[:80] if clean_body else ""
        obj.items = _clean_items(content.items)[:5]
        obj.data_label = _clean_str(content.data_label)
        obj.data_desc = _clean_str(content.data_desc)
        obj.timeline_time = _clean_str(content.timeline_time)

        # v3.1.4：Chart.js / D3 数据
        obj.chart_data = None
        obj.graph_data = None
        if visual.visual_type == VisualType.DATA_CHART and visual.structured_data:
            obj.chart_data = visual.structured_data
            has_chart = True
        if visual.visual_type == VisualType.ENTITY_GRAPH and visual.structured_data:
            obj.graph_data = visual.structured_data
            has_graph = True

        card_objs.append(obj)

    # 加载完整风格 YAML（覆盖 StyleTokens 可能缺失的字段）
    style_id = blueprint.style_tokens.style_id
    style_yaml = _load_style_yaml(style_id)

    # 构建 CSS 变量字符串（从 StyleTokens + YAML 注入）
    st = blueprint.style_tokens
    primary_color = style_yaml.get("primary_color", st.primary_color)
    secondary_color = style_yaml.get("secondary_color", st.secondary_color)
    accent_color = style_yaml.get("accent_color", st.accent_color)
    bg_color = style_yaml.get("background_color", st.background_color)
    text_color = style_yaml.get("text_color", st.text_color)
    font_size = style_yaml.get("font_size_base", st.font_size_base)
    border_radius = style_yaml.get("border_radius", st.border_radius)
    card_shadow = style_yaml.get("card_shadow", st.card_shadow)
    cover_gradient = style_yaml.get("cover_gradient", st.cover_gradient)
    summary_gradient = style_yaml.get("summary_gradient", st.summary_gradient)
    data_gradient = style_yaml.get("data_gradient", "linear-gradient(135deg, #1A1A2E 0%, #16213E 100%)")

    # 防止 None 被直接渲染为字符串 "None"（会导致 Gate2 Vision LLM 评分偏低）
    def _s(v, fallback=""):
        return v if v is not None else fallback

    css_vars = f"""
        --color-primary: {_s(primary_color, '#FF6B6B')};
        --color-secondary: {_s(secondary_color, '#FF8E53')};
        --color-accent: {_s(accent_color, '#FFB347')};
        --color-bg: {_s(bg_color, '#FFF8F0')};
        --color-text: {_s(text_color, '#1A1A1A')};
        --font-size-base: {_s(font_size, 26)}px;
        --border-radius: {_s(border_radius, 20)}px;
        --card-shadow: {_s(card_shadow, '0 2px 16px rgba(0,0,0,0.06)')};
        --cover-gradient: {_s(cover_gradient, 'linear-gradient(135deg, #FF6B6B 0%, #FF8E53 50%, #FFB347 100%)')};
        --summary-gradient: {_s(summary_gradient, 'linear-gradient(135deg, #667EEA 0%, #764BA2 100%)')};
        --data-gradient: {_s(data_gradient, 'linear-gradient(135deg, #1A1A2E 0%, #16213E 100%)')};
    """

    # CDN 脚本（按需注入）
    cdn_scripts = ""
    if has_chart:
        cdn_scripts += _CHARTJS_CDN + "\n"
    if has_graph:
        cdn_scripts += _D3_CDN + "\n"

    return {
        "cards": card_objs,
        "title": blueprint.content_tree.outline[:18] if blueprint.content_tree.outline else "信息图",
        "css_vars": css_vars,
        "style_id": st.style_id,
        "blueprint_id": blueprint.blueprint_id,
        "cdn_scripts": cdn_scripts,
    }


async def _screenshot_html_to_png(
    html_content: str, output_path: str, debug_mode: bool = False
) -> dict:
    """
    用 Playwright headless Chromium 将 HTML 截图为 PNG。
    debug_mode=True 时返回 DOM 诊断指标字典，否则返回 {}。
    """
    metrics: dict = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1080, "height": 1920}
        )
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(html_content)
            tmp_path = f.name
        try:
            await page.goto(f"file://{tmp_path}", wait_until="domcontentloaded")
            # 等待字体就绪后再截图，防止字体未加载导致文字渲染异常
            await page.evaluate("document.fonts.ready")
            await page.wait_for_timeout(300)

            if debug_mode:
                metrics = await page.evaluate("""() => {
                    const body = document.body;
                    const html = document.documentElement;
                    const infographic = document.querySelector('.infographic');
                    const cards = Array.from(document.querySelectorAll(
                        '.card-cover, .card-section, .card-data, .card-timeline, .card-summary'
                    ));
                    return {
                        viewport_height: window.innerHeight,
                        viewport_width: window.innerWidth,
                        body_scroll_height: body.scrollHeight,
                        body_client_height: body.clientHeight,
                        html_scroll_height: html.scrollHeight,
                        infographic_height: infographic ? infographic.scrollHeight : null,
                        infographic_client_height: infographic ? infographic.clientHeight : null,
                        card_count: cards.length,
                        cards: cards.map((el, i) => {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return {
                                index: i,
                                class: el.className,
                                top: Math.round(rect.top),
                                bottom: Math.round(rect.bottom),
                                height: Math.round(rect.height),
                                scroll_height: el.scrollHeight,
                                client_height: el.clientHeight,
                                overflow_x: style.overflowX,
                                overflow_y: style.overflowY,
                                is_overflowing: el.scrollHeight > el.clientHeight + 2,
                            };
                        }),
                    };
                }""")

            await page.screenshot(path=output_path, full_page=True, clip=None)
        finally:
            os.unlink(tmp_path)
            await browser.close()
    return metrics


@trace("Tool1.ImageRender")
def run_tool1_render(blueprint: Blueprint, output_path: str) -> RenderArtifact:
    """
    运行 Tool 1 图片渲染，从 Blueprint 生成 PNG。
    """
    t_start = time.time()

    # 1. 选择模板
    template_rel = _select_template(blueprint)

    # 2. 准备渲染数据
    render_data = _blueprint_to_render_data(blueprint)

    # 3. 渲染 HTML
    if template_rel is not None:
        # 使用新模板系统
        env = _get_template_env()
        try:
            template = env.get_template(template_rel)
            html = template.render(**render_data)
            print(f"[Tool1] 使用模板: {template_rel}")
        except Exception as e:
            print(f"[Tool1] 模板 {template_rel} 加载失败，回退 render.html: {e}")
            template_rel = None

    if template_rel is None:
        # 回退到 legacy render.html
        env = Environment(
            loader=FileSystemLoader(str(_HERE)),
            autoescape=True,
        )
        template = env.get_template("render.html")
        html = template.render(**render_data)
        print("[Tool1] 使用 legacy render.html 模板")

    # 3b. 注入 CDN 脚本（Chart.js / D3）到 </head> 前
    cdn_scripts = render_data.get("cdn_scripts", "")
    if cdn_scripts:
        if "</head>" in html:
            html = html.replace("</head>", cdn_scripts + "</head>", 1)
        else:
            # 无 <head> 时插到开头
            html = cdn_scripts + html
        print(f"[Tool1] CDN 脚本已注入: {cdn_scripts.count('<script') } 个")

    # 4. Playwright 截图
    asyncio.run(_screenshot_html_to_png(html, output_path))
    # debug_mode=False（默认）— 生产路径不收集 DOM 指标

    # 4b. PIL 水印后处理
    multi_source = len(blueprint.source_bundle.sources) > 1 if blueprint.source_bundle else False
    source_labels = (
        [s.source_name for s in blueprint.source_bundle.sources[:4]]
        if multi_source and blueprint.source_bundle
        else None
    )
    add_watermark(
        image_path=output_path,
        render_id=blueprint.blueprint_id,
        multi_source=multi_source,
        source_labels=source_labels,
    )

    t_end = time.time()
    render_time = t_end - t_start

    # 5. 构建 RenderArtifact
    file_size_kb = pathlib.Path(output_path).stat().st_size // 1024
    artifact = RenderArtifact(
        output_path=output_path,
        file_size_kb=file_size_kb,
        render_time_s=render_time,
        blueprint_id=blueprint.blueprint_id,
        card_count=len(blueprint.cards),
    )
    print(f"[Tool1] 渲染完成: {output_path} ({file_size_kb} KB) | 耗时: {render_time:.1f}s")
    return artifact
