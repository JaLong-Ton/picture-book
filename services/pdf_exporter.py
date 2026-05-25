import os
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# Chinese font setup — prefer Source Han Serif (思源宋体)
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = [
    # Windows
    ("ChineseFont", r"C:\Windows\Fonts\NotoSerifSC-VF.ttf", None),
    ("ChineseFont", r"C:\Windows\Fonts\Source Han Serif SC Heavy (TrueType).ttf", None),
    ("ChineseFont", r"C:\Windows\Fonts\STSONG.TTF", None),
    ("ChineseFont", r"C:\Windows\Fonts\msyh.ttc", 0),
    ("ChineseFont", r"C:\Windows\Fonts\simhei.ttf", None),
    # macOS
    ("ChineseFont", "/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("ChineseFont", "/System/Library/Fonts/PingFang.ttc", 0),
    ("ChineseFont", "/Library/Fonts/Songti.ttc", 0),
    # Linux
    ("ChineseFont", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0),
    ("ChineseFont", "/usr/share/fonts/truetype/noto/NotoSerifSC-Regular.otf", None),
    ("ChineseFont", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", None),
    ("ChineseFont", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("ChineseFont", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    # Bundled fallback (if user places a font in project/fonts/)
    ("ChineseFont", os.path.join(os.path.dirname(__file__), "..", "fonts", "NotoSerifSC-Regular.ttf"), None),
]

CN_FONT = "Helvetica"  # fallback

def _register_chinese_font():
    global CN_FONT
    for name, path, subfont in _FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            if subfont is not None:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=subfont))
            else:
                pdfmetrics.registerFont(TTFont(name, path))
            CN_FONT = name
            return
        except Exception:
            continue

_register_chinese_font()

PAGE_W, PAGE_H = A4  # 595.27 x 841.89 pt

# Text area at bottom of page
TEXT_BOX_H = 90
TEXT_BOX_Y = 24
TEXT_PAD = 18


def _wrap_text(text: str, font: str, size: int, max_width: float) -> list[str]:
    """Wrap text for mixed Chinese/Latin content, character-aware."""
    lines = []
    line = ""
    for ch in text:
        if ch == "\n":
            lines.append(line)
            line = ""
            continue
        test = line + ch
        if pdfmetrics.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = ch
    if line:
        lines.append(line)
    return lines


def build_pdf(title: str, pages: list[dict], image_buffers: list[BytesIO]) -> BytesIO:
    """Create a PDF with images full-bleed and text overlaid at bottom."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # --- cover page ---
    c.setFillColorRGB(0.12, 0.16, 0.22)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont(CN_FONT, 36)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 + 20, title)

    c.setFont(CN_FONT, 14)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 40, "A Picture Book")
    c.showPage()

    # --- content pages ---
    for i, (page_data, img_buf) in enumerate(zip(pages, image_buffers)):
        # Full-bleed image as background
        if img_buf:
            img = ImageReader(img_buf)
            c.drawImage(img, 0, 0, PAGE_W, PAGE_H)

        # Soft rounded text box at bottom
        box_x = 16
        box_w = PAGE_W - 32
        box_r = 10  # corner radius

        # Light warm background with slight transparency
        c.setFillColorRGB(1, 0.97, 0.92)  # warm cream
        c.setStrokeAlpha(0.6)
        c.setFillAlpha(0.82)
        c.roundRect(box_x, TEXT_BOX_Y, box_w, TEXT_BOX_H, box_r, fill=1, stroke=0)
        c.setFillAlpha(1)
        c.setStrokeAlpha(1)

        # Subtle border
        c.setStrokeColorRGB(0.85, 0.8, 0.72)
        c.setLineWidth(0.6)
        c.roundRect(box_x, TEXT_BOX_Y, box_w, TEXT_BOX_H, box_r, fill=0, stroke=1)

        # Text
        text = page_data.get("text", "")
        max_text_w = box_w - TEXT_PAD * 2
        font_size = 17
        leading = 26

        lines = _wrap_text(text, CN_FONT, font_size, max_text_w)
        total_h = len(lines) * leading
        start_y = TEXT_BOX_Y + (TEXT_BOX_H + total_h) / 2 - font_size

        c.setFillColorRGB(0.2, 0.18, 0.15)  # dark warm gray
        text_obj = c.beginText(box_x + TEXT_PAD, start_y)
        text_obj.setFont(CN_FONT, font_size)
        text_obj.setLeading(leading)
        for wrapped_line in lines:
            text_obj.textLine(wrapped_line)
        c.drawText(text_obj)

        # Page number
        c.setFillColorRGB(0.55, 0.5, 0.45)
        c.setFont(CN_FONT, 9)
        c.drawRightString(PAGE_W - 28, 10, str(i + 1))

        c.showPage()

    c.save()
    buf.seek(0)
    return buf
