from pathlib import Path

from pypdf import PdfReader
from services.mineru_service import MinerUService


def parse(filepath: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf(filepath, filename)
    elif ext in (".doc", ".docx"):
        return _parse_docx(filepath)
    elif ext == ".txt":
        return _parse_txt(filepath)
    else:
        raise ValueError(f"Unsupported format: {ext}")


def _parse_pdf(filepath: str, filename: str) -> str:
    """PDF: MinerU cloud (OCR) first, PyPDF2 fallback."""
    last_error = None

    # 1) MinerU cloud — handles scanned / image-based PDFs
    try:
        mineru = MinerUService()
        text = mineru.parse(filepath, filename, ocr=True)
        if text.strip():
            return text.strip()
    except Exception as e:
        last_error = e

    # 2) PyPDF2 fallback — text-based PDFs
    try:
        reader = PdfReader(filepath)
        parts = [p.extract_text() for p in reader.pages]
        text = "\n\n".join(t.strip() for t in parts if t and t.strip())
        if text.strip():
            return text.strip()
    except Exception:
        pass

    raise RuntimeError(
        f"Failed to extract text from PDF. "
        f"MinerU error: {last_error}. "
        f"Ensure MINERU_API_KEY is set in .env, or use a text-based PDF."
    )


def _parse_docx(filepath: str) -> str:
    import docx
    doc = docx.Document(filepath)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_txt(filepath: str) -> str:
    raw = Path(filepath).read_bytes()
    for enc in ("utf-8", "gbk", "gb2312", "gb18030", "latin-1"):
        try:
            return raw.decode(enc).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace").strip()
