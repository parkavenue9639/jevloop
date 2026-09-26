"""Immutable, content-addressed image captures and bounded native renditions.

Only this module resolves asset IDs to storage. Public image parts never contain
paths or binary data. Originals are retained; transport serves a metadata-free
PNG rendition (static images only, EXIF orientation applied) bounded to 2048 pixels.
"""

import base64
import hashlib
import io
import json
import os
import re
import stat
import uuid
import warnings
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from jevloop.paths import BACKEND_ROOT

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGES = 8
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_EDGE = 2048
MAX_RENDITION_BYTES = 20 * 1024 * 1024
MAX_NAME_LENGTH = 200
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
PART_FIELDS = {"type", "asset_id", "mime_type", "width", "height", "detail", "name"}
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


class AssetError(ValueError):
    """Invalid, missing, over-budget, or corrupt image evidence."""


def _asset_id(value):
    if not isinstance(value, str) or not ASSET_ID.fullmatch(value):
        raise AssetError("invalid image asset_id")
    return value


def _detail(value):
    if value not in ("auto", "high"):
        raise AssetError("image detail must be auto or high")
    return value


def _name(value):
    if not isinstance(value, str):
        raise AssetError("image name must be a string")
    return "".join(c for c in value if c.isprintable())[:MAX_NAME_LENGTH]


def validate_part(part):
    """Pure schema validation; intentionally does not touch storage."""
    if not isinstance(part, dict) or set(part) != PART_FIELDS:
        raise AssetError("image part must contain only the canonical image fields")
    if part["type"] != "image" or part["mime_type"] != "image/png":
        raise AssetError("invalid image part type or MIME type")
    _asset_id(part["asset_id"])
    _detail(part["detail"])
    if _name(part["name"]) != part["name"]:
        raise AssetError("invalid or overlong image name")
    for field in ("width", "height"):
        if type(part[field]) is not int or not 1 <= part[field] <= MAX_IMAGE_EDGE:
            raise AssetError(f"invalid image {field}")
    return dict(part)


@contextmanager
def _root(create=False):
    root = Path(os.environ.get("JEVLOOP_ASSETS_DIR", str(BACKEND_ROOT / "artifacts" / "assets")))
    try:
        if create:
            root.mkdir(parents=True, exist_ok=True)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            os.close(fd)
    except OSError as error:
        raise AssetError("image asset storage is missing or unsafe") from error


def _read(fd, name, cap):
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise AssetError("image asset file is invalid or over budget")
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise AssetError("image asset file exceeds byte budget")
    return data


def _write(fd, name, data):
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(file_fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _decode(data, *, normalize):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in SUPPORTED_FORMATS:
                    raise AssetError("unsupported image format; use PNG, JPEG, WEBP or GIF")
                if getattr(source, "is_animated", False):
                    raise AssetError("animated images are not supported; attach a static frame")
                if not normalize and source.format != "PNG":
                    raise AssetError("image rendition must be PNG")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise AssetError("image exceeds 16 megapixel decode limit")
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()  # Full decode, never merely trust a MIME/header.
                if not normalize:
                    return source.size
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
                image.info.clear()
                output = io.BytesIO()
                image.save(output, format="PNG", compress_level=6)
                return output.getvalue(), image.size
    except (OSError, SyntaxError, ValueError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        if isinstance(error, AssetError):
            raise
        raise AssetError("invalid or corrupt image data") from error


def _load(asset_id):
    _asset_id(asset_id)
    try:
        with _root() as root_fd:
            fd = os.open(asset_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                manifest = json.loads(_read(fd, "manifest.json", 4096))
                original = _read(fd, "original", MAX_IMAGE_BYTES)
                rendition = _read(fd, "image.png", MAX_RENDITION_BYTES)
            finally:
                os.close(fd)
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or hashlib.sha256(original).hexdigest() != asset_id
                or hashlib.sha256(rendition).hexdigest() != manifest.get("rendition_sha256")):
            raise AssetError("image asset integrity check failed")
        dimensions = _decode(rendition, normalize=False)
        if dimensions != (manifest.get("width"), manifest.get("height")) or max(dimensions) > MAX_IMAGE_EDGE:
            raise AssetError("image asset dimensions are corrupt")
        return manifest, rendition, len(original)
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, AssetError):
            raise
        raise AssetError("image asset is missing or corrupt") from error


def ingest_image(data: bytes, name: str = "", detail: str = "auto") -> dict:
    """Decode then durably publish an original and its normalized rendition."""
    _detail(detail)
    name = _name(name)
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise AssetError("image requires 1..10 MiB of binary data")
    asset_id = hashlib.sha256(data).hexdigest()
    rendition, (width, height) = _decode(data, normalize=True)
    if len(rendition) > MAX_RENDITION_BYTES:
        raise AssetError("normalized image exceeds rendition byte budget")
    manifest = {"version": 1, "width": width, "height": height,
                "rendition_sha256": hashlib.sha256(rendition).hexdigest()}
    with _root(create=True) as root_fd:
        temporary = ".capture-" + uuid.uuid4().hex
        os.mkdir(temporary, mode=0o700, dir_fd=root_fd)
        fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            _write(fd, "original", data)
            _write(fd, "image.png", rendition)
            _write(fd, "manifest.json", json.dumps(manifest, sort_keys=True).encode())
            os.fsync(fd)
            try:
                # Publishing a complete nonempty directory is atomic. A concurrent
                # winner is never overwritten; its contents are verified below.
                os.rename(temporary, asset_id, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            except OSError:
                # Only a valid existing capture can make a failed publish succeed.
                _load(asset_id)
            os.fsync(root_fd)
        finally:
            os.close(fd)
            try:
                temp_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            else:
                try:
                    for filename in ("original", "image.png", "manifest.json"):
                        try:
                            os.unlink(filename, dir_fd=temp_fd)
                        except FileNotFoundError:
                            pass
                finally:
                    os.close(temp_fd)
                os.rmdir(temporary, dir_fd=root_fd)
    return resolve_image({"type": "image", "asset_id": asset_id, "mime_type": "image/png",
                          "width": width, "height": height, "detail": detail, "name": name})


def resolve_image(part: dict) -> dict:
    """Resolve storage truth; caller dimensions and MIME never override it."""
    if not isinstance(part, dict):
        raise AssetError("image part must be an object")
    if part.get("type") != "image" or set(part) != PART_FIELDS:
        raise AssetError("image part must contain only the canonical image fields")
    detail, name = _detail(part.get("detail")), _name(part.get("name"))
    manifest, _, _size = _load(part.get("asset_id"))
    return {"type": "image", "asset_id": part["asset_id"], "mime_type": "image/png",
            "width": manifest["width"], "height": manifest["height"], "detail": detail, "name": name}


def read_image(asset_id) -> tuple[bytes, str]:
    _, data, _size = _load(asset_id)
    return data, "image/png"


def image_size(part: dict) -> int:
    """Original capture bytes for aggregate request/session budgeting."""
    validate_part(part)
    return _load(part["asset_id"])[2]


def image_data_url(part: dict) -> str:
    validate_part(part)
    data, mime_type = read_image(part["asset_id"])
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
