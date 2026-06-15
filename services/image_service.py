"""Image generation — delegates to the provider selected by IMAGE_PROVIDER.

Keeps the same public API (draw / download_image) so callers don't change.
"""

from io import BytesIO

from dotenv import load_dotenv

from services.image_provider import get_provider

load_dotenv()

_provider = None


def _ensure_provider():
    global _provider
    if _provider is None:
        _provider = get_provider()
        print(f"[image] using provider: {_provider.name}")
    return _provider


def draw(prompt: str, aspect_ratio: str = "16:9",
         image_size: str = "2k", images: list[str] | None = None) -> str:
    """Generate an image synchronously. Returns image URL. Retries 3x."""
    return _ensure_provider().generate(prompt, aspect_ratio, image_size, images)


def download_image(url: str) -> BytesIO:
    """Download image from URL, return BytesIO buffer. Retries 3x."""
    return _ensure_provider().download(url)
