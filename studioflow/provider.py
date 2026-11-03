"""Supported image models and the source-image editing provider.

The API and frontend consume the same capability list. Keep provider-specific
arguments and image postprocessing here so queued jobs can be tested offline.
"""

import base64
import binascii
import io
import uuid
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageOps


DEFAULT_MODEL = "gpt-image-2.5-flare"
REVISION_MODEL = "gpt-image-2.5-sunburst"
ALLOWED_QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
MODEL_CONFIG = [
    {
        "id": DEFAULT_MODEL,
        "label": "Flare · faster generation",
        "qualities": list(ALLOWED_QUALITIES),
    },
    {
        "id": REVISION_MODEL,
        "label": "Sunburst · precision editing",
        "qualities": list(ALLOWED_QUALITIES),
    },
    {"id": "gpt-image-2", "label": "GPT Image 2", "qualities": ["low", "medium", "high", "auto"]},
    {"id": "gpt-image-1.5", "label": "GPT Image 1.5", "qualities": ["low", "medium", "high", "auto"]},
    {"id": "gpt-image-1-mini", "label": "GPT Image 1 Mini · lower cost", "qualities": ["low", "medium", "high", "auto"]},
    {"id": "chatgpt-image-latest", "label": "ChatGPT Image Latest", "qualities": ["low", "medium", "high", "auto"]},
]
ALLOWED_MODELS = tuple(model["id"] for model in MODEL_CONFIG)

# The first size is requested from the provider; the second is the final asset.
# Preserve the existing provider resolutions and associated request sizes.
PRESETS = {
    "square": ("1024x1024", (2000, 2000)),
    "landscape": ("1536x1024", (1536, 1024)),
    "banner": ("1536x1024", (1464, 600)),
    "mobile": ("1024x1024", (600, 450)),
    "portrait": ("1024x1536", (1024, 1536)),
}

# Per-model capabilities, rather than a model-name prefix heuristic. Existing
# general exports retain their request sizes; A+ can request a closer aspect ratio.
FLEXIBLE_SIZE_MODELS = {DEFAULT_MODEL, REVISION_MODEL, "gpt-image-2"}


def api_image_size(model, canvas, *, aplus=False):
    if aplus and model in FLEXIBLE_SIZE_MODELS:
        return {"banner": "1536x640", "mobile": "1024x768"}.get(canvas, PRESETS[canvas][0])
    return PRESETS[canvas][0]


def validate_settings(model: str, quality: str, canvas: str) -> None:
    """Reject unsupported combinations before files or paid jobs are created."""
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Unsupported model. Supported: {', '.join(ALLOWED_MODELS)}")
    capabilities = next(item for item in MODEL_CONFIG if item["id"] == model)
    if quality not in capabilities["qualities"]:
        raise ValueError("Unsupported quality for this image model")
    if canvas not in PRESETS:
        raise ValueError("Unsupported output size")


def prepare_image_input(filepath: Path) -> tuple[str, bytes, str]:
    """Build an SDK file argument while retaining its real media type."""
    filepath = Path(filepath)
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    try:
        media_type = media_types[filepath.suffix.lower()]
    except KeyError:
        raise ValueError("Provider inputs must be PNG, JPG or WEBP images") from None
    return filepath.name, filepath.read_bytes(), media_type


def render_live(
    inputs: Sequence[Path],
    output: Path,
    prompt: str,
    model: str = DEFAULT_MODEL,
    quality: str = "medium",
    canvas: str = "square",
    api_key: str = "",
    *,
    client: Any = None,
    aplus: bool = False,
    on_request: Any = None,
    on_response: Any = None,
) -> None:
    """Edit source images and atomically write a complete, uncropped JPEG.

    ``inputs`` may include a previous render followed by the original product.
    Inject ``client`` for offline tests; otherwise a client is created and closed
    for this call. GPT image models return base64 without ``response_format``.
    """
    validate_settings(model, quality, canvas)
    if not 1 <= len(inputs) <= 16:
        raise ValueError("Image editing requires 1–16 source images")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32000:
        raise ValueError("Image prompt must contain 1–32000 characters")

    image_payload = [prepare_image_input(Path(path)) for path in inputs]
    own_client = client is None
    if own_client:
        if not api_key:
            raise ValueError("Live image generation requires an API key")
        from openai import OpenAI

        # Retrying an ambiguous image request may incur another charge. Leave
        # retries explicit at the job level rather than hiding them in the SDK.
        client = OpenAI(api_key=api_key, timeout=180, max_retries=0)
    try:
        if on_request is not None:
            on_request()
        response = client.images.edit(
            model=model,
            image=image_payload if len(image_payload) > 1 else image_payload[0],
            prompt=prompt,
            size=api_image_size(model, canvas, aplus=aplus),
            quality=quality,
            n=1,
        )
        # Persist metering before decoding, exporting or even closing the client.
        if on_response is not None:
            on_response(response)
    finally:
        if own_client:
            client.close()

    data = getattr(response, "data", None)
    encoded = getattr(data[0], "b64_json", None) if data else None
    if not encoded:
        raise RuntimeError("No image data returned from the image provider")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise RuntimeError("Image provider returned invalid base64 image data") from exc

    output = Path(output)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            image = Image.new("RGBA", oriented.size, "white")
            image.alpha_composite(oriented)
            # Padding preserves the entire composition at every export ratio.
            image = ImageOps.pad(
                image.convert("RGB"),
                PRESETS[canvas][1],
                method=Image.Resampling.LANCZOS,
                color="white",
                centering=(0.5, 0.5),
            )
            image.save(temporary, "JPEG", quality=95, optimize=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
