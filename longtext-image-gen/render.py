"""
v3.1.0 MVP — render.py
将结构化卡片 JSON 通过 Jinja2 + Playwright 渲染为 PNG（小红书 1080 宽）。
"""

import asyncio
import json
import os
import pathlib
import tempfile
from typing import Any

from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

# 模板路径（与 render.py 同目录）
_HERE = pathlib.Path(__file__).parent
_TEMPLATE_FILE = _HERE / "render.html"


def _load_template() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_HERE)),
        autoescape=True,
    )
    return env


def render_html(cards_data: dict[str, Any]) -> str:
    """
    将卡片数据渲染为 HTML 字符串。
    cards_data 结构：{"title": ..., "subtitle": ..., "cards": [...]}
    """
    env = _load_template()
    template = env.get_template("render.html")

    cards = cards_data.get("cards", [])
    # 确保每张卡片都有必要字段，缺失时填充默认值
    # 重要：将 dict 转为简单对象，避免 Jinja2 的 card.items 歧义（dict.items 方法）
    class CardObj:
        pass

    card_objs = []
    for card in cards:
        obj = CardObj()
        obj.card_type = card.get("card_type", "section")
        obj.title = card.get("title", "")
        obj.body = card.get("body", "")
        obj.data_label = card.get("data_label", "")
        obj.data_desc = card.get("data_desc", "")
        obj.timeline_time = card.get("timeline_time", "")
        obj.items = card.get("items", []) or []
        card_objs.append(obj)

    return template.render(cards=card_objs)


async def _screenshot_html_to_png(html_content: str, output_path: str) -> None:
    """用 Playwright headless Chromium 将 HTML 截图为 PNG。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1080, "height": 800}  # 高度会被内容撑开
        )

        # 写到临时文件，避免 data URL 长度限制
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(html_content)
            tmp_path = f.name

        try:
            await page.goto(f"file://{tmp_path}")
            # 等待字体/布局稳定
            await page.wait_for_timeout(500)

            # 截整个 body（full page 模式）
            await page.screenshot(
                path=output_path,
                full_page=True,
                clip=None,
            )
        finally:
            os.unlink(tmp_path)
            await browser.close()


def render_to_png(cards_data: dict[str, Any], output_path: str) -> str:
    """
    端到端：卡片 JSON → PNG 文件。
    返回输出路径字符串。
    """
    html = render_html(cards_data)
    asyncio.run(_screenshot_html_to_png(html, output_path))
    return output_path
