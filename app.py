import base64
import json
import os
import requests
import shutil
import sqlite3
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


PHOTO_REFERENCE_BLOCK = """
## 人物参考（重要）
参考附带的人物照片，将照片中人物的面部特征、发型、肤色融入到绘本角色设计中。
角色应保持迪士尼皮克斯 3D 动画风格，但要能辨认出是照片中的人物。
照片中的人物应自然地出现在绘本场景中，与故事内容融为一体。"""


def build_image_prompt(page: dict, page_num: int, character_desc: str = "", has_photo: bool = False) -> str:
    prompt = IMAGE_PROMPT_TEMPLATE.format(
        page_num=page_num,
        title=page.get("title", ""),
        content=page.get("text", ""),
        scene=page.get("illustration", ""),
        character_block=character_desc,
    )
    if has_photo:
        prompt += PHOTO_REFERENCE_BLOCK
    return prompt

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Job persistence (SQLite) + auto-cleanup
# ---------------------------------------------------------------------------
class JobStore:
    """Persists job state to SQLite; in-memory dict stays as fast cache."""

    def __init__(self, db_path: str = "data/jobs.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id     TEXT PRIMARY KEY,
                stage      TEXT NOT NULL DEFAULT 'starting',
                progress   INTEGER DEFAULT 0,
                title      TEXT,
                filename   TEXT,
                pdf_path   TEXT,
                doc_path   TEXT,
                error      TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    def create(self, job_id: str, doc_path: str):
        now = time.time()
        jobs[job_id] = {"stage": "starting", "progress": 0}
        self._conn.execute(
            "INSERT INTO jobs (job_id, stage, progress, doc_path, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (job_id, "starting", 0, doc_path, now, now),
        )
        self._conn.commit()

    def update(self, job_id: str, **fields):
        if not fields:
            return
        jobs.setdefault(job_id, {}).update(fields)
        fields["updated_at"] = time.time()
        set_clause = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE job_id=?",
            (*fields.values(), job_id),
        )
        self._conn.commit()

    def get(self, job_id: str) -> dict | None:
        return jobs.get(job_id)

    def snapshot(self, job: dict) -> dict:
        keys = ("stage", "progress", "current_page", "total_pages",
                "current_character", "total_characters", "title", "error")
        resp = {k: job[k] for k in keys if k in job}
        if job.get("stage") == "done":
            resp["filename"] = job.get("filename", "book.pdf")
        return resp

    def load_existing(self):
        """Restore jobs from SQLite into memory on startup."""
        cur = self._conn.execute(
            "SELECT job_id, stage, progress, title, filename, pdf_path, doc_path, error FROM jobs"
        )
        for row in cur.fetchall():
            jid, stage, prog, title, fn, pdf, doc, err = row
            if stage in ("done", "error"):
                continue
            if stage == "outline_ready":
                # Restore outline-ready jobs so user can still preview & generate
                jobs[jid] = {"stage": "outline_ready", "progress": prog, "title": title or ""}
                continue
            # Mark other incomplete jobs as interrupted
            jobs[jid] = {"stage": "interrupted", "progress": prog, "title": title or ""}
            self._conn.execute(
                "UPDATE jobs SET stage='interrupted', updated_at=? WHERE job_id=?",
                (time.time(), jid),
            )
        self._conn.commit()

    def cleanup(self, ttl_hours: float = 24):
        cutoff = time.time() - ttl_hours * 3600
        cur = self._conn.execute(
            "SELECT job_id, doc_path, pdf_path FROM jobs WHERE created_at < ?", (cutoff,)
        )
        rows = cur.fetchall()
        for jid, doc, pdf in rows:
            for p in (doc, pdf):
                if p:
                    Path(p).unlink(missing_ok=True)
            for d in (OUTPUT_DIR / jid, UPLOAD_DIR / jid):
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
            # Also remove the original upload file
            for f in UPLOAD_DIR.glob(f"{jid}.*"):
                f.unlink(missing_ok=True)
            jobs.pop(jid, None)
        if rows:
            self._conn.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
            self._conn.commit()
        return len(rows)


def _run_cleanup_loop():
    ttl = float(os.environ.get("JOB_TTL_HOURS", "24"))
    while True:
        time.sleep(3600)
        try:
            n = job_store.cleanup(ttl)
            if n:
                print(f"[cleanup] removed {n} expired job(s)")
        except Exception as e:
            print(f"[cleanup] error: {e}")


job_store = JobStore()


def parse_and_outline(job_id: str, doc_path: str, filename: str):
    """Phase 1-2: parse document → generate outline. Stops at outline_ready."""
    job = jobs[job_id]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        job["stage"] = "parsing"
        job["progress"] = 5
        job_store.update(job_id, stage="parsing", progress=5)
        text = parse_document(doc_path, filename)
        if not text.strip():
            raise ValueError("No text found in document.")

        outline_path = job_dir / "outline.json"
        if outline_path.exists():
            outline = json.loads(outline_path.read_text(encoding="utf-8"))
        else:
            job["stage"] = "outline"
            job["progress"] = 30
            job_store.update(job_id, stage="outline", progress=30)
            outline = generate_outline(text)
            outline_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")

        job["title"] = outline.get("title", "My Picture Book")
        job["total_pages"] = len(outline.get("pages", []))
        job["stage"] = "outline_ready"
        job["progress"] = 100
        job_store.update(job_id, stage="outline_ready", progress=100, title=job["title"])

    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)
        job_store.update(job_id, stage="error", error=str(e))


def generate_book(job_id: str, photo_urls: list[str] | None = None):
    """Phase 3-5: character sheets → page images → PDF. Reads outline from disk."""
    job = jobs[job_id]
    job_dir = OUTPUT_DIR / job_id

    try:
        outline_path = job_dir / "outline.json"
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
        pages = outline.get("pages", [])
        characters = outline.get("characters", [])
        job["title"] = outline.get("title", "My Picture Book")
        job["total_pages"] = len(pages)

        char_desc = build_character_description(characters)

        # ── Phase 3: Character reference sheets ──
        if characters:
            job["stage"] = "character_sheets"
            job["total_characters"] = len(characters)
            job_store.update(job_id, stage="character_sheets")
            for ci, char in enumerate(characters):
                job["current_character"] = ci + 1
                job["progress"] = int((ci / max(len(characters), 1)) * 10)
                if not job.get("character_sheets") or ci >= len(job.get("character_sheets", [])):
                    sheet_prompt = build_character_sheet_prompt(char)
                    url = draw(prompt=sheet_prompt)
                    job.setdefault("character_sheets", []).append(
                        {"name": char.get("name", ""), "url": url}
                    )

        # Download character sheets as base64 for use as reference images
        ref_images = list(photo_urls) if photo_urls else []
        for sheet in job.get("character_sheets", []):
            try:
                resp = requests.get(sheet["url"], timeout=30)
                resp.raise_for_status()
                b64 = base64.b64encode(resp.content).decode()
                ref_images.append(f"data:image/png;base64,{b64}")
            except Exception:
                pass  # skip failed downloads, still generate with text desc

        # ── Phase 4: Page images (incremental + resume) ──
        image_buffers = []
        for i, page in enumerate(pages):
            job["stage"] = "generating_images"
            job["current_page"] = i + 1
            if i == 0:
                job_store.update(job_id, stage="generating_images")
            job["progress"] = 10 + int((i / max(len(pages), 1)) * 70)

            img_path = job_dir / f"page_{i}.png"

            if img_path.exists():
                buf = BytesIO(img_path.read_bytes())
                image_buffers.append(buf)
                continue

            # draw() 只调一次，避免重复生成浪费 API
            try:
                prompt = build_image_prompt(page, i + 1, char_desc, has_photo=bool(ref_images))
                url = draw(prompt=prompt, images=ref_images or None)
            except Exception as e:
                print(f"[page {i+1}] draw failed: {e}")
                image_buffers.append(None)
                continue

            # draw 成功，下载失败时用同一 URL 重试，不重新生成
            try:
                buf = download_image(url)
                img_path.write_bytes(buf.getvalue())
                image_buffers.append(buf)
            except Exception as e:
                print(f"[page {i+1}] download failed: {e}")
                image_buffers.append(None)

        # ── Phase 5: Build PDF ──
        job["stage"] = "building_pdf"
        job["progress"] = 85
        job_store.update(job_id, stage="building_pdf", progress=85)
        pdf_buf = build_pdf(job["title"], pages, image_buffers)

        pdf_out = job_dir / "book.pdf"
        pdf_out.write_bytes(pdf_buf.read())

        job["stage"] = "done"
        job["progress"] = 100
        job["filename"] = f"{job['title']}.pdf"
        job["pdf_path"] = str(pdf_out)
        job_store.update(job_id, stage="done", progress=100,
                         filename=job["filename"], pdf_path=job["pdf_path"])

    except Exception as e:
        job["stage"] = "error"
        job["error"] = str(e)
        job_store.update(job_id, stage="error", error=str(e))


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
    # secure_filename strips non-ASCII chars, so "后裔射日.docx" → "docx" (no dot).
    # We only need the filename for extension detection in parse_document,
    # so use job_id + original extension as the safe filename.
    filename = f"{job_id}{ext}"
    doc_path = UPLOAD_DIR / f"{job_id}{ext}"
    file.save(str(doc_path))

    # Handle optional photo uploads
    photo_urls = []
    photos = request.files.getlist("photos")
    if photos:
        photo_dir = UPLOAD_DIR / job_id / "photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        for pi, pf in enumerate(photos):
            if not pf or not pf.filename:
                continue
            photo_bytes = pf.read()
            photo_ext = Path(pf.filename).suffix.lower() or ".jpg"
            photo_path = photo_dir / f"photo_{pi}{photo_ext}"
            photo_path.write_bytes(photo_bytes)
            b64 = base64.b64encode(photo_bytes).decode()
            mime = pf.content_type or "image/jpeg"
            photo_urls.append(f"data:{mime};base64,{b64}")

    job_store.create(job_id, str(doc_path))
    # Store photo_urls in memory for later use by generate_book
    jobs[job_id]["photo_urls"] = photo_urls
    threading.Thread(
        target=parse_and_outline, args=(job_id, str(doc_path), filename), daemon=True
    ).start()

    return jsonify({"job_id": job_id})


@app.get("/api/outline/<job_id>")
def get_outline(job_id):
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("stage") != "outline_ready":
        return jsonify({"error": "Outline not ready"}), 400
    outline_path = OUTPUT_DIR / job_id / "outline.json"
    if not outline_path.exists():
        return jsonify({"error": "Outline file not found"}), 404
    return jsonify(json.loads(outline_path.read_text(encoding="utf-8")))


@app.post("/api/outline/<job_id>")
def save_outline(job_id):
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("stage") != "outline_ready":
        return jsonify({"error": "Cannot edit outline in current stage"}), 400
    data = request.get_json(silent=True)
    if not data or "pages" not in data:
        return jsonify({"error": "Invalid outline data"}), 400
    outline_path = OUTPUT_DIR / job_id / "outline.json"
    outline_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    job["title"] = data.get("title", job.get("title", ""))
    job["total_pages"] = len(data.get("pages", []))
    job_store.update(job_id, title=job["title"])
    return jsonify({"ok": True})


@app.post("/api/generate/<job_id>")
def generate(job_id):
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("stage") != "outline_ready":
        return jsonify({"error": "Not ready to generate"}), 400
    photo_urls = job.get("photo_urls", [])
    job["progress"] = 0
    job_store.update(job_id, stage="character_sheets", progress=0)
    threading.Thread(
        target=generate_book, args=(job_id, photo_urls), daemon=True
    ).start()
    return jsonify({"ok": True})


@app.post("/api/retry/<job_id>")
def retry(job_id):
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("stage") != "error":
        return jsonify({"error": "Job is not in error state"}), 400
    # Reset to generating state; generate_book() will skip already-generated pages
    job["stage"] = "generating_images"
    job.pop("error", None)
    job["progress"] = 0
    job_store.update(job_id, stage="generating_images", progress=0, error=None)
    photo_urls = job.get("photo_urls", [])
    threading.Thread(
        target=generate_book, args=(job_id, photo_urls), daemon=True
    ).start()
    return jsonify({"ok": True})


@app.get("/api/status/<job_id>")
def status(job_id):
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job_store.snapshot(job))


@app.get("/api/stream/<job_id>")
def stream(job_id):
    def generate():
        last = ""
        while True:
            job = job_store.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return
            snapshot = json.dumps(job_store.snapshot(job), ensure_ascii=False)
            if snapshot != last:
                yield f"data: {snapshot}\n\n"
                last = snapshot
            if job.get("stage") in ("done", "error", "outline_ready"):
                return
            time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/download/<job_id>")
def download(job_id):
    job = job_store.get(job_id)
    if not job or job["stage"] != "done":
        return jsonify({"error": "Not ready"}), 404

    return send_file(
        job["pdf_path"],
        as_attachment=True,
        download_name=job.get("filename", "picture_book.pdf"),
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    # Restore jobs from previous session
    job_store.load_existing()

    # Start background cleanup thread (default: purge jobs older than 24h)
    cleanup_thread = threading.Thread(target=_run_cleanup_loop, daemon=True)
    cleanup_thread.start()

    debug = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    app.run(debug=debug, port=5000)
