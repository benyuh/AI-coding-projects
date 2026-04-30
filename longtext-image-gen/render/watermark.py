"""
render/watermark.py — PIL 水印 + 品牌标识后处理（v3.1.4）

按 PRD 4.9.1.7：
- 右下角品牌水印（宽度 6%，透明度 60%）
- render_id 后缀文字
- 多源场景：顶部信源标注 + 底部免责声明
"""

from __future__ import annotations

import pathlib
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    import logging
    logging.getLogger(__name__).warning("Pillow 未安装，水印功能跳过（pip install Pillow 可启用）")

_HERE = pathlib.Path(__file__).parent.parent


def _get_font(size: int, bold: bool = False) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """尝试加载系统字体，失败时使用默认字体。"""
    # macOS 系统字体候选
    font_candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in font_candidates:
        if pathlib.Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def add_watermark(
    image_path: str,
    output_path: Optional[str] = None,
    render_id: str = "",
    brand_text: str = "Ducc AI",
    opacity: float = 0.55,
    multi_source: bool = False,
    source_labels: Optional[list[str]] = None,
) -> str:
    """
    为 PNG 图片添加水印和品牌标识。

    Args:
        image_path: 输入 PNG 路径
        output_path: 输出路径（None 时覆盖原文件）
        render_id: Blueprint ID，作为水印后缀
        brand_text: 品牌名称
        opacity: 水印透明度（0-1）
        multi_source: 是否添加多源信源标注
        source_labels: 多源信源列表

    Returns:
        输出文件路径
    """
    if not _PIL_AVAILABLE:
        return image_path

    output_path = output_path or image_path

    try:
        img = Image.open(image_path).convert("RGBA")
        width, height = img.size

        # 创建透明水印层
        watermark_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(watermark_layer)

        # ── 右下角品牌水印 ─────────────────────────────────────────────────────
        brand_font_size = max(20, int(width * 0.018))
        brand_font = _get_font(brand_font_size)
        watermark_text = f"✦ {brand_text}" + (f" · {render_id[:8]}" if render_id else "")

        # 计算文字尺寸
        bbox = draw.textbbox((0, 0), watermark_text, font=brand_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # 右下角位置（留 16px 边距）
        x = width - text_w - 20
        y = height - text_h - 16

        # 半透明背景圆角框
        pad = 8
        draw.rounded_rectangle(
            [x - pad, y - pad, x + text_w + pad, y + text_h + pad],
            radius=6,
            fill=(0, 0, 0, int(80 * opacity)),
        )
        draw.text(
            (x, y),
            watermark_text,
            font=brand_font,
            fill=(255, 255, 255, int(255 * opacity)),
        )

        # ── 多源信源标注（顶部）─────────────────────────────────────────────
        if multi_source and source_labels:
            source_font_size = max(18, int(width * 0.016))
            source_font = _get_font(source_font_size)
            label_text = "信源: " + " · ".join(source_labels[:4])
            draw.text(
                (20, 16),
                label_text,
                font=source_font,
                fill=(100, 100, 100, int(255 * opacity)),
            )

        # 合并水印层
        img = Image.alpha_composite(img, watermark_layer)

        # 保存为 PNG（保持透明通道）或 RGB
        final = img.convert("RGB")
        final.save(output_path, "PNG", optimize=False)

        return output_path

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"水印添加失败，返回原图: {e}")
        return image_path
