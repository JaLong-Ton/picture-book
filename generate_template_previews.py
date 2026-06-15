"""One-shot script: generate preview images for template marketplace."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from services.image_service import draw, download_image
from app import template_store, seed_templates, DEFAULT_STYLE_BLOCK

PREVIEW_SCENE = """你是一位专业的儿童绘本插画师。

## 任务
为绘本模板市场生成一张预览展示图。

## 场景描述
一只可爱的小狐狸站在秋天的森林里，周围有飘落的红叶和温暖的阳光。
小狐狸大大的眼睛，毛茸茸的尾巴，表情好奇而快乐。
背景是金色的树林，远处有一条小溪。

{style_block}

## 要求
- 画面构图饱满，适合作为模板市场的展示缩略图
- 色彩鲜明，能充分展现该画风的特色
- 16:9 比例，高清分辨率

## 严格禁止
- 禁止画面中出现任何文字、标签、水印
- 禁止对话气泡"""


def main():
    # Ensure templates are seeded
    seed_templates(template_store)
    templates = template_store.get_all()

    out_dir = Path("outputs/templates")
    out_dir.mkdir(parents=True, exist_ok=True)

    for t in templates:
        tid = t["template_id"]
        preview_path = out_dir / t["preview_image"]

        if preview_path.exists():
            print(f"[{tid}] preview already exists, skipping")
            continue

        style_block = t.get("image_prompt_style", DEFAULT_STYLE_BLOCK)
        prompt = PREVIEW_SCENE.format(style_block=style_block)

        print(f"[{tid}] generating preview for '{t['name']}'...")
        try:
            url = draw(prompt=prompt, aspect_ratio="16:9", image_size="2k")
            buf = download_image(url)
            preview_path.write_bytes(buf.getvalue())
            print(f"[{tid}] saved to {preview_path}")
        except Exception as e:
            print(f"[{tid}] FAILED: {e}")

    print("\nDone!")


if __name__ == "__main__":
    main()
