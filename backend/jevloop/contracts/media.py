"""Pure image evidence contracts; never reads files or interprets user text."""

import json
import re
from copy import deepcopy

IMAGE_FIELDS = ("type", "asset_id", "mime_type", "width", "height", "detail", "name")
MAX_IMAGES = 8


class MediaUnavailable(RuntimeError):
    """Required visual evidence/capability is unavailable; do not omit it."""


def validate_image_part(part):
    if not isinstance(part, dict) or part.get("type") != "image":
        raise MediaUnavailable("Expected a typed image part.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(part.get("asset_id", ""))):
        raise MediaUnavailable("Invalid image asset identity.")
    if part.get("mime_type") not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise MediaUnavailable("Unsupported image MIME type.")
    if any(type(part.get(key)) is not int or not 1 <= part[key] <= 16384 for key in ("width", "height")):
        raise MediaUnavailable("Invalid image dimensions.")
    if part.get("detail") not in {"auto", "high"}:
        raise MediaUnavailable("Unsupported image detail.")
    if not isinstance(part.get("name"), str) or len(part["name"]) > 200:
        raise MediaUnavailable("Invalid image display name.")
    return {key: deepcopy(part[key]) for key in IMAGE_FIELDS}


def image_parts(value):
    if not isinstance(value, list) or len(value) > MAX_IMAGES:
        raise MediaUnavailable(f"Expected at most {MAX_IMAGES} image parts.")
    return [validate_image_part(part) for part in value]


def record_images(record):
    """Only code-owned user/result envelopes admit images, not nested data."""
    if record.get("role") == "user":
        return image_parts(record.get("images", []))
    if record.get("role") == "tool":
        content = record.get("content")
        try:
            result = json.loads(content) if isinstance(content, str) else content
        except (ValueError, TypeError):
            return []
        if (isinstance(result, dict) and result.get("status") in {"ready", "done"}
                and not result.get("error") and not result.get("superseded")
                and result.get("effect_disposition") not in {"UNKNOWN", "NOT_APPLIED", "DENIED"}):
            return image_parts(result.get("images", []))
    return []


def transcript_images(records):
    images = []
    for record in records:
        images.extend(record_images(record))
    # History is evidence, not a request batch. Re-reading a retained asset must
    # not exhaust a lifetime image-occurrence quota or erase earlier observations.
    return images
