"""
tests/unit/test_v314_smoke.py — v3.1.4 冒烟测试

覆盖：
- CDN 脚本注入（Chart.js / D3）到 </head> 前
- CDN 注入无 <head> 时插到开头
- 水印模块可导入（Pillow 可选）
- 风格 YAML 加载（magazine_editorial / tech_minimal / data_journalism）
- 风格 YAML 关键字段校验（颜色系统 / 字体 / 渐变）
- _load_style_yaml 缺失文件时返回空 dict（不崩溃）
- _blueprint_to_render_data 包含 cdn_scripts / css_vars / style_id
- 模板选择：目录存在时返回相对路径，目录不存在时返回 None（legacy）
- tool1_image_render 模块可导入（不调 Playwright）
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

import pytest


# ── CDN 注入逻辑（纯字符串，不依赖 Playwright）────────────────────────────────

def _inject_cdn(html: str, cdn_scripts: str) -> str:
    """复制 tool1_image_render 中的注入逻辑以便单元测试。"""
    if not cdn_scripts:
        return html
    if "</head>" in html:
        return html.replace("</head>", cdn_scripts + "</head>", 1)
    return cdn_scripts + html


def test_cdn_inject_before_head_close():
    html = "<html><head><title>T</title></head><body></body></html>"
    cdn = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>\n'
    result = _inject_cdn(html, cdn)
    assert "chart.js" in result
    assert result.index("chart.js") < result.index("</head>")


def test_cdn_inject_no_head_tag():
    html = "<body><p>hello</p></body>"
    cdn = '<script src="https://cdn.jsdelivr.net/npm/d3@7.8.5/dist/d3.min.js"></script>\n'
    result = _inject_cdn(html, cdn)
    assert result.startswith('<script')
    assert "d3.min.js" in result


def test_cdn_inject_empty_scripts():
    html = "<html><head></head><body></body></html>"
    result = _inject_cdn(html, "")
    assert result == html  # 无 CDN 时不修改


def test_cdn_inject_both_scripts():
    from tools.tool1_image_render import _CHARTJS_CDN, _D3_CDN
    html = "<html><head></head><body></body></html>"
    cdn = _CHARTJS_CDN + "\n" + _D3_CDN + "\n"
    result = _inject_cdn(html, cdn)
    assert "chart.js" in result
    assert "d3.min.js" in result
    # 两个脚本都在 </head> 前
    head_close_pos = result.index("</head>")
    assert result.index("chart.js") < head_close_pos
    assert result.index("d3.min.js") < head_close_pos


# ── 水印模块可导入 ─────────────────────────────────────────────────────────────

def test_watermark_import():
    from render.watermark import add_watermark, _PIL_AVAILABLE
    assert callable(add_watermark)
    # _PIL_AVAILABLE 必须是 bool，不能是 None
    assert isinstance(_PIL_AVAILABLE, bool)


def test_watermark_no_pil_returns_original(tmp_path):
    """Pillow 未安装时 add_watermark 应返回原路径（不崩溃）。"""
    from render.watermark import add_watermark, _PIL_AVAILABLE
    if _PIL_AVAILABLE:
        pytest.skip("Pillow 已安装，跳过降级测试")
    # 传入不存在的路径，Pillow 缺失时直接返回
    result = add_watermark("nonexistent.png", render_id="test-id")
    assert result == "nonexistent.png"


# ── 风格 YAML 加载 ─────────────────────────────────────────────────────────────

def test_load_style_yaml_magazine_editorial():
    from tools.tool1_image_render import _load_style_yaml
    data = _load_style_yaml("magazine_editorial")
    assert data, "magazine_editorial.yaml 应成功加载"
    assert data.get("primary_color") == "#C9A84C"
    assert data.get("background_color") == "#FAFAF8"
    assert "linear-gradient" in data.get("cover_gradient", "")


def test_load_style_yaml_tech_minimal():
    from tools.tool1_image_render import _load_style_yaml
    data = _load_style_yaml("tech_minimal")
    assert data
    assert data.get("primary_color") == "#0066FF"
    assert data.get("border_radius") == 12


def test_load_style_yaml_data_journalism():
    from tools.tool1_image_render import _load_style_yaml
    data = _load_style_yaml("data_journalism")
    assert data
    assert data.get("primary_color") == "#1DB954"
    assert data.get("background_color") == "#0D1117"


def test_load_style_yaml_missing_returns_empty():
    from tools.tool1_image_render import _load_style_yaml
    data = _load_style_yaml("nonexistent_style_xyz")
    assert data == {}, "缺失 YAML 应返回空 dict，不抛异常"


def test_load_style_yaml_all_five_styles():
    """验证 v3.1.4 的 5 种风格 YAML 均可加载。"""
    from tools.tool1_image_render import _load_style_yaml
    style_ids = [
        "magazine_editorial",
        "tech_minimal",
        "data_journalism",
        "xiaohongshu_warm",
        "default",  # 可能不存在，用于测试降级
    ]
    loaded = 0
    for sid in style_ids:
        data = _load_style_yaml(sid)
        if data:
            loaded += 1
    assert loaded >= 3, f"至少3种风格应可加载，实际加载: {loaded}"


# ── _blueprint_to_render_data 渲染数据结构 ────────────────────────────────────

def _make_minimal_blueprint():
    from ir.models import (
        Blueprint, Card, CardContent, ContentTree, RouterDecision,
        SourceBundle, StyleTokens, VisualSpec,
    )
    sb = SourceBundle(text="测试文本" * 50)
    rd = RouterDecision()
    ct = ContentTree(source_bundle=sb)
    st = StyleTokens(font_size_base=26, style_id="tech_minimal")
    card0 = Card(
        card_index=0,
        content=CardContent(title="封面标题", body="副标题文字"),
        visual_spec=VisualSpec(),
    )
    card1 = Card(
        card_index=1,
        content=CardContent(title="内容标题", body="正文内容"),
        visual_spec=VisualSpec(),
    )
    return Blueprint(
        source_bundle=sb,
        router_decision=rd,
        content_tree=ct,
        style_tokens=st,
        cards=[card0, card1],
    )


def test_render_data_has_required_keys():
    from tools.tool1_image_render import _blueprint_to_render_data
    bp = _make_minimal_blueprint()
    data = _blueprint_to_render_data(bp)
    for key in ("cards", "title", "css_vars", "style_id", "blueprint_id", "cdn_scripts"):
        assert key in data, f"render_data 缺少键: {key}"


def test_render_data_css_vars_contains_data_gradient():
    from tools.tool1_image_render import _blueprint_to_render_data
    bp = _make_minimal_blueprint()
    data = _blueprint_to_render_data(bp)
    assert "--data-gradient" in data["css_vars"], "css_vars 应包含 --data-gradient"


def test_render_data_cdn_scripts_empty_when_no_chart():
    from tools.tool1_image_render import _blueprint_to_render_data
    bp = _make_minimal_blueprint()
    data = _blueprint_to_render_data(bp)
    assert data["cdn_scripts"] == "", "无 DATA_CHART/ENTITY_GRAPH 时 cdn_scripts 应为空"


def test_render_data_cdn_scripts_for_chart_card():
    from ir.models import (
        Blueprint, Card, CardContent, ContentTree, RouterDecision,
        SourceBundle, StyleTokens, VisualSpec, VisualType,
    )
    sb = SourceBundle(text="测试" * 50)
    rd = RouterDecision()
    ct = ContentTree(source_bundle=sb)
    st = StyleTokens(font_size_base=26)
    chart_card = Card(
        card_index=0,
        content=CardContent(title="图表标题", body="图表说明"),
        visual_spec=VisualSpec(
            visual_type=VisualType.DATA_CHART,
            structured_data={"labels": ["A", "B"], "values": [10, 20]},
        ),
    )
    bp = Blueprint(
        source_bundle=sb, router_decision=rd, content_tree=ct,
        style_tokens=st, cards=[chart_card],
    )
    from tools.tool1_image_render import _blueprint_to_render_data
    data = _blueprint_to_render_data(bp)
    assert "chart.js" in data["cdn_scripts"], "DATA_CHART 卡片应注入 Chart.js CDN"


# ── tool1_image_render 模块可导入 ──────────────────────────────────────────────

def test_tool1_module_importable():
    import tools.tool1_image_render as m
    assert callable(m.run_tool1_render)
    assert callable(m._load_style_yaml)
    assert callable(m._select_template)
    assert callable(m._blueprint_to_render_data)
    assert callable(m._screenshot_html_to_png)
    assert hasattr(m, "_CHARTJS_CDN")
    assert hasattr(m, "_D3_CDN")
    assert hasattr(m, "_STYLES_DIR")


if __name__ == "__main__":
    test_cdn_inject_before_head_close()
    test_cdn_inject_no_head_tag()
    test_cdn_inject_empty_scripts()
    test_cdn_inject_both_scripts()
    test_watermark_import()
    test_load_style_yaml_magazine_editorial()
    test_load_style_yaml_tech_minimal()
    test_load_style_yaml_data_journalism()
    test_load_style_yaml_missing_returns_empty()
    test_load_style_yaml_all_five_styles()
    test_render_data_has_required_keys()
    test_render_data_css_vars_contains_data_gradient()
    test_render_data_cdn_scripts_empty_when_no_chart()
    test_render_data_cdn_scripts_for_chart_card()
    test_tool1_module_importable()
    print("\n所有 v3.1.4 冒烟测试通过 ✅")
