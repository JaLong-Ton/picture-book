import os
import time
from io import BytesIO

import requests
from dotenv import load_dotenv

load_dotenv()

API_ENDPOINT = "/v1/api/generate"
DEFAULT_BASE = "https://grsaiapi.com"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds, exponential: 2 → 4 → 8

# Reusable session for connection pooling
_session = requests.Session()


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NANO_BANANA_API_KEY')}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return os.getenv("NANO_BANANA_API_BASE", DEFAULT_BASE)


def _retry(fn, retries: int = MAX_RETRIES):
    """Call fn() with retries and exponential backoff."""
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
    raise last_err


def draw(prompt: str, aspect_ratio: str = "16:9",
         image_size: str = "2k", images: list[str] | None = None) -> str:
    """Generate an image synchronously. Returns image URL. Retries 3x."""
    body = {
        "model": os.getenv("NANO_BANANA_MODEL", "nano-banana-pro"),
        "prompt": prompt,
        "aspectRatio": aspect_ratio,
        "imageSize": image_size,
        "replyType": "json",
    }
    if images:
        body["images"] = images

    def _do():
        resp = _session.post(
            f"{_base_url()}{API_ENDPOINT}",
            json=body,
            headers=_headers(),
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "")
        if status in ("failed", "violation"):
            raise RuntimeError(f"Image generation failed: {data.get('error', data)}")

        results = data.get("results", [])
        if results and results[0].get("url"):
            return results[0]["url"]

        raise RuntimeError(f"No image URL in response: {data}")

    return _retry(_do)


def download_image(url: str) -> BytesIO:
    """Download image from URL, return BytesIO buffer. Retries 3x."""
    def _do():
        resp = _session.get(url, timeout=30)
        resp.raise_for_status()
        buf = BytesIO(resp.content)
        buf.seek(0)
        return buf

    return _retry(_do)
