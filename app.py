import json
import os
import time
import uuid
import threading
from io import BytesIO
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, Response
from werkzeug.utils import secure_filename

from services.document_parser import parse as parse_document
from services.outline_generator import (
    generate_outline,
    build_character_sheet_prompt,
    build_character_description,
)
from services.image_service import draw, download_image
from services.pdf_exporter import build_pdf

IMAGE_PROMPT_TEMPLATE = """你是一位专业的儿童绘本插画师。

## 任务
为绘本的第 {page_num} 页绘制插图。

## 场景描述
标题：{title}
文字：{content}
场景：{scene}

{character_block}

## 风格要求
- 迪士尼皮克斯 3D 动画风格
- 明亮温暖的色调，主色调为橙色、绿色、蓝色
- 角色有大眼睛、圆润的线条
- 背景简洁，有柔和的光线
- 16:9 比例，4K 分辨率

## 场景文字（可选）
允许画面中出现与场景融为一体的装饰性文字，如路牌、招牌、店铺名、书名等。
这些文字应该是画面的一部分，自然地出现在场景中，不是旁白或字幕。

## 严格禁止
- 禁止在画面底部或顶部叠加旁白、字幕、叙述文字
- 禁止对话气泡、标签、水印
- 禁止出现 Markdown 符号（# * 等）
- 禁止风格突变"""


def build_image_prompt(page: dict, page_num: int, character_desc: str = "") -> str:
    return IMAGE_PROMPT_TEMPLATE.format(
        page_num=page_num,
        title=page.get("title", ""),
        content=page.get("text", ""),
        scene=page.get("illustration", ""),
        character_block=character_desc,
    )

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

jobs: dict[str, dict] = {}


def process_job(job_id: str, doc_path: str, filename: str):
    """Background task: parse → outline → character sheets → images → PDF."""
    job = jobs[job_id]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        # ── Phase 1: Document parsing ──
        job["stage"] = "parsing"
        job["progress"] = 5
        text = parse_document(doc_path, filename)
        if not text.strip():
            raise ValueError("No text found in document.")

        # ── Phase 2: Outline generation (with resume) ──
        outline_path = job_dir / "outline.json"
        if outline_path.exists():
            outline = json.loads(outline_path.read_text(encoding="utf-8"))
        else:
            job["stage"] = "outline"
            job["progress"] = 10
            outline = generate_outline(text)
            outline_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")

        pages = outline.get("pages", [])
        characters = outline.get("characters", [])
        job["title"] = outline.get("title", "My Picture Book")
        job["total_pages"] = len(pages)

        # Build character consistency description
        char_desc = build_character_description(characters)

        # ── Phase 3: Character reference sheets ──
        if characters:
            job["stage"] = "character_sheets"
            job["total_characters"] = len(characters)
            for ci, char in enumerate(characters):
                job["current_character"] = ci + 1
                job["progress"] = 12 + int((ci / max(len(characters), 1)) * 5)
                if not job.get("character_sheets") or ci >= len(job.get("character_sheets", [])):
                    sheet_prompt = build_character_sheet_prompt(char)
                    url = draw(prompt=sheet_prompt)
                    job.setdefault("character_sheets", []).append(
                        {"name": char.get("name", ""), "url": url}
                    )

        # ── Phase 4: Page images (incremental + resume) ──
        image_buffers = []
        for i, page in enumerate(pages):
            job["stage"] = "generating_images"
            job["current_page"] = i + 1
            job["progress"] = 20 + int((i / max(len(pages), 1)) * 60)

            img_path = job_dir / f"page_{i}.png"

            # Resume: skip already-generated pages
            if img_path.exists():
                buf = BytesIO(img_path.read_bytes())
                image_buffers.append(buf)
                continue

            # Generate with per-page retry (2 extra attempts)
            page_err = None
            for attempt in range(3):
                try:
                    prompt = build_image_prompt(page, i + 1, char_desc)
                    url = draw(prompt=prompt)
                    buf = download_image(url)
                    # Incremental save to disk
                    img_path.write_bytes(buf.getvalue())
                    image_buffers.append(buf)
                    page_err = None
                    break
                except Exception as e:
                    page_err = e
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))

            if page_err is not None:
                # Page failed after retries — skip with None placeholder
                image_buffers.append(None)

        # ── Phase 5: Build PDF ──
        job["stage"] = "building_pdf"
        job["progress"] = 85
        pdf_buf = build_pdf(job["title"], pages, image_buffers)

        pdf_out = job_dir / "book.pdf"
        pdf_out.write_bytes(pdf_buf.read())

        job["stage"] = "done"
        job["progress"] = 100
        job["filename"] = f"{job['title']}.pdf"
        job["pdf_path"] = str(pdf_out)

    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".doc", ".docx", ".txt"):
        return jsonify({"error": "Only PDF, Word (.doc/.docx), and TXT files are supported"}), 400

    job_id = uuid.uuid4().hex[:12]
    filename = secure_filename(file.filename)
    doc_path = UPLOAD_DIR / f"{job_id}{ext}"
    file.save(str(doc_path))

    jobs[job_id] = {"stage": "starting", "progress": 0}
    threading.Thread(
        target=process_job, args=(job_id, str(doc_path), filename), daemon=True
    ).start()

    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_job_snapshot(job))


def _job_snapshot(job: dict) -> dict:
    keys = ("stage", "progress", "current_page", "total_pages",
            "current_character", "total_characters", "title", "error")
    resp = {k: job[k] for k in keys if k in job}
    if job.get("stage") == "done":
        resp["filename"] = job.get("filename", "book.pdf")
    return resp


@app.get("/api/stream/<job_id>")
def stream(job_id):
    def generate():
        last = ""
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return
            snapshot = json.dumps(_job_snapshot(job), ensure_ascii=False)
            if snapshot != last:
                yield f"data: {snapshot}\n\n"
                last = snapshot
            if job.get("stage") in ("done", "error"):
                return
            time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or job["stage"] != "done":
        return jsonify({"error": "Not ready"}), 404

    return send_file(
        job["pdf_path"],
        as_attachment=True,
        download_name=job.get("filename", "picture_book.pdf"),
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    app.run(debug=debug, port=5000)
