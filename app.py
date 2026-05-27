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
{style_block}

## 场景文字（可选）
允许画面中出现与场景融为一体的装饰性文字，如路牌、招牌、店铺名、书名等。
这些文字应该是画面的一部分，自然地出现在场景中，不是旁白或字幕。

## 严格禁止
- 禁止在画面底部或顶部叠加旁白、字幕、叙述文字
- 禁止对话气泡、标签、水印
- 禁止出现 Markdown 符号（# * 等）
- 禁止风格突变"""

DEFAULT_STYLE_BLOCK = """- 迪士尼皮克斯 3D 动画风格
- 明亮温暖的色调，主色调为橙色、绿色、蓝色
- 角色有大眼睛、圆润的线条
- 背景简洁，有柔和的光线
- 16:9 比例，4K 分辨率"""


PHOTO_REFERENCE_BLOCK = """
## 人物参考（重要）
参考附带的人物照片，将照片中人物的面部特征、发型、肤色融入到绘本角色设计中。
角色应保持当前绘本的画风风格，但要能辨认出是照片中的人物。
照片中的人物应自然地出现在绘本场景中，与故事内容融为一体。"""


def build_image_prompt(page: dict, page_num: int, character_desc: str = "", has_photo: bool = False, template: dict | None = None) -> str:
    style_block = template["image_prompt_style"] if template and template.get("image_prompt_style") else DEFAULT_STYLE_BLOCK
    prompt = IMAGE_PROMPT_TEMPLATE.format(
        page_num=page_num,
        title=page.get("title", ""),
        content=page.get("text", ""),
        scene=page.get("illustration", ""),
        character_block=character_desc,
        style_block=style_block,
    )
    if has_photo:
        prompt += PHOTO_REFERENCE_BLOCK
    return prompt

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB upload limit

jobs: dict[str, dict] = {}
_job_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Job persistence (SQLite) + auto-cleanup
# ---------------------------------------------------------------------------
class JobStore:
    """Persists job state to SQLite; in-memory dict stays as fast cache."""

    def __init__(self, db_path: str = "data/jobs.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                stage       TEXT NOT NULL DEFAULT 'starting',
                progress    INTEGER DEFAULT 0,
                title       TEXT,
                filename    TEXT,
                pdf_path    TEXT,
                doc_path    TEXT,
                photo_urls  TEXT,
                error       TEXT,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
        """)
        # Migrate: add photo_urls column if missing (for existing databases)
        try:
            self._conn.execute("SELECT photo_urls FROM jobs LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN photo_urls TEXT")
            self._conn.commit()
        # Migrate: add template_id column if missing
        try:
            self._conn.execute("SELECT template_id FROM jobs LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN template_id TEXT")
            self._conn.commit()
        self._conn.commit()

    def create(self, job_id: str, doc_path: str, photo_urls: list[str] | None = None, template_id: str = "default"):
        now = time.time()
        jobs[job_id] = {"stage": "starting", "progress": 0, "template_id": template_id}
        photo_json = json.dumps(photo_urls, ensure_ascii=False) if photo_urls else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (job_id, stage, progress, doc_path, photo_urls, template_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, "starting", 0, doc_path, photo_json, template_id, now, now),
            )
            self._conn.commit()

    def update(self, job_id: str, **fields):
        if not fields:
            return
        jobs.setdefault(job_id, {}).update(fields)
        fields["updated_at"] = time.time()
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
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
        with self._lock:
            cur = self._conn.execute(
                "SELECT job_id, stage, progress, title, filename, pdf_path, doc_path, photo_urls, template_id, error FROM jobs"
            )
            rows = cur.fetchall()
        for row in rows:
            jid, stage, prog, title, fn, pdf, doc, photo_json, template_id, err = row
            if stage in ("done", "error"):
                continue
            restored = {"stage": "interrupted", "progress": prog, "title": title or ""}
            if photo_json:
                restored["photo_urls"] = json.loads(photo_json)
            restored["template_id"] = template_id or "default"
            if stage == "outline_ready":
                restored["stage"] = "outline_ready"
            jobs[jid] = restored
            if stage != "outline_ready":
                with self._lock:
                    self._conn.execute(
                        "UPDATE jobs SET stage='interrupted', updated_at=? WHERE job_id=?",
                        (time.time(), jid),
                    )
        with self._lock:
            self._conn.commit()

    def cleanup(self, ttl_hours: float = 24):
        cutoff = time.time() - ttl_hours * 3600
        with self._lock:
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
            for f in UPLOAD_DIR.glob(f"{jid}.*"):
                f.unlink(missing_ok=True)
            jobs.pop(jid, None)
        if rows:
            with self._lock:
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


def _get_job_lock(job_id: str) -> threading.Lock:
    """Get or create a per-job lock to prevent duplicate thread starts."""
    with _global_lock:
        if job_id not in _job_locks:
            _job_locks[job_id] = threading.Lock()
        return _job_locks[job_id]


# ---------------------------------------------------------------------------
# Template store
# ---------------------------------------------------------------------------
class TemplateStore:
    """Persists template definitions to SQLite."""

    def __init__(self, db_path: str = "data/jobs.db"):
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                template_id          TEXT PRIMARY KEY,
                name                 TEXT NOT NULL,
                description          TEXT,
                category             TEXT,
                image_prompt_style   TEXT NOT NULL,
                character_prompt_style TEXT NOT NULL,
                color_palette        TEXT,
                preview_image        TEXT,
                is_default           INTEGER DEFAULT 0,
                sort_order           INTEGER DEFAULT 0,
                created_at           REAL NOT NULL
            )
        """)
        self._conn.commit()

    def get_all(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT template_id, name, description, category, image_prompt_style, "
                "character_prompt_style, color_palette, preview_image, is_default, sort_order "
                "FROM templates ORDER BY sort_order"
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, template_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT template_id, name, description, category, image_prompt_style, "
                "character_prompt_style, color_palette, preview_image, is_default, sort_order "
                "FROM templates WHERE template_id=?", (template_id,)
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_default(self) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT template_id, name, description, category, image_prompt_style, "
                "character_prompt_style, color_palette, preview_image, is_default, sort_order "
                "FROM templates WHERE is_default=1 LIMIT 1"
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    @staticmethod
    def _row_to_dict(row) -> dict:
        keys = ("template_id", "name", "description", "category", "image_prompt_style",
                "character_prompt_style", "color_palette", "preview_image", "is_default", "sort_order")
        d = dict(zip(keys, row))
        if d.get("color_palette"):
            try:
                d["color_palette"] = json.loads(d["color_palette"])
            except (json.JSONDecodeError, TypeError):
                d["color_palette"] = []
        else:
            d["color_palette"] = []
        return d


def seed_templates(store: TemplateStore):
    """Insert default templates if the table is empty."""
    existing = store.get_all()
    if existing:
        return

    now = time.time()
    templates = [
        {
            "template_id": "default",
            "name": "迪士尼3D",
            "description": "明亮温暖的3D动画风格，适合大多数绘本故事",
            "category": "3D动画",
            "image_prompt_style": DEFAULT_STYLE_BLOCK,
            "character_prompt_style": """## 风格
迪士尼皮克斯 3D 动画风格
明亮温暖的色调，橙色/绿色/蓝色配色
大而有神的眼睛，圆润柔和的线条""",
            "color_palette": json.dumps(["#FF8A65", "#4DB6AC", "#7986CB"]),
            "preview_image": "default.png",
            "is_default": 1,
            "sort_order": 0,
        },
        {
            "template_id": "watercolor",
            "name": "水彩手绘",
            "description": "柔和晕染的水彩质感，梦幻温馨",
            "category": "手绘风",
            "image_prompt_style": """- 水彩手绘风格，笔触自然，颜料晕染效果
- 柔和的暖色调，以粉色、淡蓝、鹅黄为主
- 纸质纹理背景，边缘有水彩飞白效果
- 梦幻柔和的光影
- 16:9 比例，4K 分辨率""",
            "character_prompt_style": """## 风格
水彩手绘风格，笔触自然，颜料晕染效果
柔和的暖色调，粉色/淡蓝/鹅黄配色
纸质纹理，水彩飞白效果""",
            "color_palette": json.dumps(["#F8BBD0", "#B3E5FC", "#FFF9C4"]),
            "preview_image": "watercolor.png",
            "is_default": 0,
            "sort_order": 1,
        },
        {
            "template_id": "ink-wash",
            "name": "中国水墨",
            "description": "传统水墨画风，意境深远，适合古典故事",
            "category": "国风",
            "image_prompt_style": """- 中国传统水墨画风格，笔墨浓淡相宜
- 黑白灰为主色调，点缀少量朱红、石青
- 宣纸质感，留白意境深远
- 山水、花鸟元素自然融入背景
- 16:9 比例，4K 分辨率""",
            "character_prompt_style": """## 风格
中国传统水墨画风格，笔墨浓淡相宜
黑白灰为主，点缀朱红、石青
宣纸质感，留白意境""",
            "color_palette": json.dumps(["#424242", "#B0BEC5", "#E53935"]),
            "preview_image": "ink-wash.png",
            "is_default": 0,
            "sort_order": 2,
        },
        {
            "template_id": "anime",
            "name": "日系动漫",
            "description": "精致的日系动漫画风，色彩鲜明",
            "category": "动漫",
            "image_prompt_style": """- 日系动漫插画风格，精致细腻的线条
- 鲜明饱和的色彩，渐变丰富
- 大而有神的眼睛，精致的五官比例
- 唯美光影，樱花/星空等浪漫元素
- 16:9 比例，4K 分辨率""",
            "character_prompt_style": """## 风格
日系动漫插画风格，精致细腻的线条
鲜明饱和的色彩，渐变丰富
大而有神的眼睛，精致五官""",
            "color_palette": json.dumps(["#E91E63", "#9C27B0", "#2196F3"]),
            "preview_image": "anime.png",
            "is_default": 0,
            "sort_order": 3,
        },
        {
            "template_id": "paper-cut",
            "name": "剪纸风",
            "description": "中国传统剪纸艺术，层次分明的平面美学",
            "category": "传统艺术",
            "image_prompt_style": """- 中国剪纸艺术风格，层次分明的平面构成
- 大红、金色、翠绿等传统色彩
- 镂空剪影效果，边缘整齐利落
- 民间装饰纹样融入背景
- 16:9 比例，4K 分辨率""",
            "character_prompt_style": """## 风格
中国剪纸艺术风格，平面构成
大红、金色、翠绿传统色彩
镂空剪影效果，边缘整齐""",
            "color_palette": json.dumps(["#D32F2F", "#FFD600", "#2E7D32"]),
            "preview_image": "paper-cut.png",
            "is_default": 0,
            "sort_order": 4,
        },
        {
            "template_id": "crayon",
            "name": "蜡笔童趣",
            "description": "童真蜡笔画风，适合低龄儿童绘本",
            "category": "童趣",
            "image_prompt_style": """- 蜡笔绘画风格，笔触粗犷有质感
- 明亮大胆的色彩，红黄蓝绿为主
- 稚拙可爱的造型，比例夸张有趣
- 纸张纹理明显，有蜡笔涂抹的颗粒感
- 16:9 比例，4K 分辨率""",
            "character_prompt_style": """## 风格
蜡笔绘画风格，笔触粗犷有质感
明亮大胆的色彩，红黄蓝绿
稚拙可爱的造型，比例夸张""",
            "color_palette": json.dumps(["#F44336", "#FFEB3B", "#2196F3"]),
            "preview_image": "crayon.png",
            "is_default": 0,
            "sort_order": 5,
        },
    ]

    with store._lock:
        for t in templates:
            store._conn.execute(
                "INSERT INTO templates (template_id, name, description, category, "
                "image_prompt_style, character_prompt_style, color_palette, preview_image, "
                "is_default, sort_order, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t["template_id"], t["name"], t["description"], t["category"],
                 t["image_prompt_style"], t["character_prompt_style"], t["color_palette"],
                 t["preview_image"], t["is_default"], t["sort_order"], now),
            )
        store._conn.commit()
    print(f"[templates] seeded {len(templates)} default templates")


def parse_and_outline(job_id: str, doc_path: str, filename: str, template_id: str = "default"):
    """Phase 1-2: parse document → generate outline. Stops at outline_ready."""
    job = jobs[job_id]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    # Look up template for style-aware outline generation
    template = template_store.get(template_id) if template_store else None

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
            outline = generate_outline(text, template=template)
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


def generate_book(job_id: str, photo_urls: list[str] | None = None, lock: threading.Lock | None = None, template_id: str = "default"):
    """Phase 3-5: character sheets → page images → PDF. Reads outline from disk."""
    job = jobs[job_id]
    job_dir = OUTPUT_DIR / job_id

    # Look up template
    template = template_store.get(template_id) if template_store else None

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
            char_style = template["character_prompt_style"] if template and template.get("character_prompt_style") else ""
            for ci, char in enumerate(characters):
                job["current_character"] = ci + 1
                job["progress"] = int((ci / max(len(characters), 1)) * 10)
                if not job.get("character_sheets") or ci >= len(job.get("character_sheets", [])):
                    sheet_prompt = build_character_sheet_prompt(char, style_override=char_style)
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
                prompt = build_image_prompt(page, i + 1, char_desc, has_photo=bool(ref_images), template=template)
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
    finally:
        if lock:
            try:
                lock.release()
            except RuntimeError:
                pass


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

    template_id = request.form.get("template_id", "default")
    job_store.create(job_id, str(doc_path), photo_urls, template_id=template_id)
    threading.Thread(
        target=parse_and_outline, args=(job_id, str(doc_path), filename, template_id), daemon=True
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
    lock = _get_job_lock(job_id)
    if not lock.acquire(blocking=False):
        return jsonify({"error": "Generation already in progress"}), 409
    try:
        if job.get("stage") != "outline_ready":
            return jsonify({"error": "Not ready to generate"}), 400
        photo_urls = job.get("photo_urls", [])
        template_id = job.get("template_id", "default")
        job["progress"] = 0
        job_store.update(job_id, stage="character_sheets", progress=0)
        threading.Thread(
            target=generate_book, args=(job_id, photo_urls, lock, template_id), daemon=True
        ).start()
    except Exception:
        lock.release()
        raise
    return jsonify({"ok": True})


@app.post("/api/retry/<job_id>")
def retry(job_id):
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    lock = _get_job_lock(job_id)
    if not lock.acquire(blocking=False):
        return jsonify({"error": "Generation already in progress"}), 409
    try:
        if job.get("stage") != "error":
            return jsonify({"error": "Job is not in error state"}), 400
        job["stage"] = "generating_images"
        job.pop("error", None)
        job["progress"] = 0
        job_store.update(job_id, stage="generating_images", progress=0, error=None)
        photo_urls = job.get("photo_urls", [])
        template_id = job.get("template_id", "default")
        threading.Thread(
            target=generate_book, args=(job_id, photo_urls, lock, template_id), daemon=True
        ).start()
    except Exception:
        lock.release()
        raise
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


@app.get("/gallery/<filename>")
def gallery_image(filename):
    """Serve gallery preview images."""
    gallery_dir = OUTPUT_DIR / "gallery"
    file_path = gallery_dir / filename
    if not file_path.exists():
        return jsonify({"error": "Image not found"}), 404
    return send_file(str(file_path), mimetype="image/png")


@app.get("/api/templates")
def list_templates():
    """Return all available templates."""
    templates = template_store.get_all()
    return jsonify(templates)


@app.get("/api/templates/<template_id>")
def get_template(template_id):
    """Return a single template by ID."""
    t = template_store.get(template_id)
    if not t:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(t)


@app.get("/templates/preview/<filename>")
def template_preview(filename):
    """Serve template preview images."""
    preview_dir = OUTPUT_DIR / "templates"
    file_path = preview_dir / filename
    if not file_path.exists():
        return jsonify({"error": "Preview not found"}), 404
    return send_file(str(file_path), mimetype="image/png")


template_store = TemplateStore()

if __name__ == "__main__":
    # Restore jobs from previous session
    job_store.load_existing()

    # Seed default templates
    seed_templates(template_store)

    # Start background cleanup thread (default: purge jobs older than 24h)
    cleanup_thread = threading.Thread(target=_run_cleanup_loop, daemon=True)
    cleanup_thread.start()

    debug = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    app.run(debug=debug, port=5000)
