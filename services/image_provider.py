"""Pluggable image generation providers.

Add new providers by subclassing ImageProvider and registering in _PROVIDERS.
Selection is driven by the IMAGE_PROVIDER env var (default: nano-banana).
"""

import os
import time
import base64
from abc import ABC, abstractmethod
from io import BytesIO
from typing import BinaryIO

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config helpers — new generic names first, with backward-compat fallback
# ---------------------------------------------------------------------------

_PROVIDER_ENV = "IMAGE_PROVIDER"


def _env(name: str, fallback: str | None = None) -> str | None:
    return os.getenv(name) or None


def _env_legacy(new: str, old: str, default: str | None = None) -> str:
    """Read `new` first, fall back to `old`, then `default`."""
    val = _env(new) or os.getenv(old) or default
    if val is None:
        raise RuntimeError(
            f"Missing config: set {new}={old} (or the legacy name '{old}') in .env"
        )
    if os.getenv(old) and not os.getenv(new):
        print(f"[deprecation] '{old}' is deprecated, rename to '{new}' in .env")
    return val


def get_env_image_api_key() -> str:
    return _env_legacy("IMAGE_API_KEY", "NANO_BANANA_API_KEY")


def get_env_image_api_base() -> str:
    return _env_legacy("IMAGE_API_BASE", "NANO_BANANA_API_BASE", "https://grsaiapi.com")


def get_env_image_model() -> str:
    return _env_legacy("IMAGE_MODEL", "NANO_BANANA_MODEL", "gpt-image-2")


# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------

RETRY_BASE_DELAY = 2
MAX_RETRIES = 3


class ImageProvider(ABC):
    name: str = ""

    @abstractmethod
    def generate(self, prompt: str, aspect_ratio: str = "16:9",
                 image_size: str = "2k",
                 reference_images: list[str] | None = None) -> str:
        """Generate an image. Return its public URL. May block for 60s+."""
        ...

    @abstractmethod
    def download(self, url: str) -> BytesIO:
        """Download an image from a URL into a BytesIO buffer."""
        ...

    def _retry(self, fn):
        """Run fn() with retries and exponential backoff."""
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                return fn()
            except Exception as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        raise last_err  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Nano Banana (default — backward compatible)
# ---------------------------------------------------------------------------

_API_ENDPOINT = "/v1/api/generate"
_session = requests.Session()


class NanoBananaProvider(ImageProvider):
    name = "nano-banana"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {get_env_image_api_key()}",
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return get_env_image_api_base()

    def generate(self, prompt: str, aspect_ratio: str = "16:9",
                 image_size: str = "2k",
                 reference_images: list[str] | None = None) -> str:
        body = {
            "model": get_env_image_model(),
            "prompt": prompt,
            "aspectRatio": aspect_ratio,
            "imageSize": image_size,
            "replyType": "json",
        }
        if reference_images:
            body["images"] = reference_images

        def _do():
            resp = _session.post(
                f"{self._base_url()}{_API_ENDPOINT}",
                json=body,
                headers=self._headers(),
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

        return self._retry(_do)

    def download(self, url: str) -> BytesIO:
        def _do():
            resp = _session.get(url, timeout=30)
            resp.raise_for_status()
            buf = BytesIO(resp.content)
            buf.seek(0)
            return buf

        return self._retry(_do)


# ---------------------------------------------------------------------------
# OpenAI DALL-E 3
# ---------------------------------------------------------------------------


class OpenAIDalleProvider(ImageProvider):
    name = "openai-dalle"

    # Map our generic ratios / sizes to OpenAI's
    _RATIO_TO_SIZE = {
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "1:1": "1024x1024",
    }

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {get_env_image_api_key()}",
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return get_env_image_api_base()

    def generate(self, prompt: str, aspect_ratio: str = "16:9",
                 image_size: str = "2k",
                 reference_images: list[str] | None = None) -> str:
        # DALL-E 3 doesn't support reference images directly — if user
        # provides them, we still pass the text prompt (the character
        # description block in the prompt handles consistency).
        size = self._RATIO_TO_SIZE.get(aspect_ratio, "1792x1024")

        body = {
            "model": get_env_image_model(),
            "prompt": prompt,
            "n": 1,
            "size": size,
        }

        def _do():
            resp = _session.post(
                f"{self._base_url()}/v1/images/generations",
                json=body,
                headers=self._headers(),
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            url = data.get("data", [{}])[0].get("url")
            if not url:
                raise RuntimeError(f"No image URL in response: {data}")
            return url

        return self._retry(_do)

    def download(self, url: str) -> BytesIO:
        def _do():
            resp = _session.get(url, timeout=30)
            resp.raise_for_status()
            buf = BytesIO(resp.content)
            buf.seek(0)
            return buf

        return self._retry(_do)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[ImageProvider]] = {
    "nano-banana": NanoBananaProvider,
    "openai-dalle": OpenAIDalleProvider,
}


def get_provider() -> ImageProvider:
    """Return the ImageProvider instance selected by IMAGE_PROVIDER env var."""
    name = os.getenv(_PROVIDER_ENV, "nano-banana").strip().lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        known = ", ".join(_PROVIDERS)
        raise RuntimeError(
            f"Unknown IMAGE_PROVIDER '{name}'. Known providers: {known}"
        )
    return cls()
