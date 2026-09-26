"""Real image decoding and durable immutable asset references; no model calls."""

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from jevloop.storage import assets


def png(size=(32, 16), color="red", format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=format)
    return output.getvalue()


@pytest.fixture(autouse=True)
def asset_dir(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    monkeypatch.setenv("JEVLOOP_ASSETS_DIR", str(root))
    return root


def test_capture_is_content_addressed_and_normalized(asset_dir):
    original = png((2400, 1200), format="JPEG")
    part = assets.ingest_image(original, "photo.jpg", "high")
    assert part == {"type": "image", "asset_id": hashlib.sha256(original).hexdigest(),
                    "mime_type": "image/png", "width": 2048, "height": 1024,
                    "detail": "high", "name": "photo.jpg"}
    assert (asset_dir / part["asset_id"] / "original").read_bytes() == original
    data, mime = assets.read_image(part["asset_id"])
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (2048, 1024) and image.format == "PNG"
    assert mime == "image/png"
    assert assets.image_size(part) == len(original)
    assert assets.image_data_url(part).startswith("data:image/png;base64,")
    assert assets.validate_part(part) == part


def test_repeated_and_parallel_captures_never_replace_source(asset_dir):
    original = png()
    first = assets.ingest_image(original, "first")
    files = list((asset_dir / first["asset_id"]).iterdir())
    before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in files}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: assets.ingest_image(original, "another"), range(8)))
    assert {p["asset_id"] for p in results} == {first["asset_id"]}
    assert {p.name: (p.stat().st_ino, p.read_bytes()) for p in files} == before
    assert not list(asset_dir.glob(".capture-*"))


def test_resolve_uses_storage_dimensions_and_mime():
    part = assets.ingest_image(png())
    assert assets.resolve_image({**part, "width": 9000, "height": -1, "mime_type": "text/plain"}) == part
    assert assets.ingest_image(png(), "a\nb" + "x" * 300)["name"] == "ab" + "x" * 198


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n", png()[:40]])
def test_invalid_or_truncated_input_has_no_asset(data, asset_dir):
    with pytest.raises(assets.AssetError):
        assets.ingest_image(data)
    assert not asset_dir.exists()


def test_input_byte_and_decode_pixel_budgets(monkeypatch):
    with pytest.raises(assets.AssetError, match="10 MiB"):
        assets.ingest_image(b"x" * (assets.MAX_IMAGE_BYTES + 1))
    monkeypatch.setattr(assets, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(assets.AssetError, match="megapixel"):
        assets.ingest_image(png((11, 10)))


@pytest.mark.parametrize("asset_id", ["../escape", "/tmp/a", "https://example.com/a", "z" * 64, "a" * 64])
def test_unknown_and_path_ids_fail(asset_id):
    with pytest.raises(assets.AssetError):
        assets.read_image(asset_id)


@pytest.mark.parametrize("file", ["original", "image.png", "manifest.json"])
def test_corrupt_asset_fails_closed(asset_dir, file):
    part = assets.ingest_image(png())
    (asset_dir / part["asset_id"] / file).write_bytes(b"corrupt")
    with pytest.raises(assets.AssetError):
        assets.resolve_image(part)


def test_symlink_files_and_directories_are_rejected(asset_dir, tmp_path):
    part = assets.ingest_image(png())
    image = asset_dir / part["asset_id"] / "image.png"
    external = tmp_path / "external.png"
    image.rename(external)
    image.symlink_to(external)
    with pytest.raises(assets.AssetError):
        assets.read_image(part["asset_id"])
    root = asset_dir / part["asset_id"]
    root.rename(tmp_path / "external-dir")
    root.symlink_to(tmp_path / "external-dir", target_is_directory=True)
    with pytest.raises(assets.AssetError):
        assets.ingest_image(png())


def test_schema_check_is_pure_and_does_not_resolve_storage(asset_dir):
    part = {"type": "image", "asset_id": "a" * 64, "mime_type": "image/png",
            "width": 1, "height": 1, "detail": "auto", "name": ""}
    assert assets.validate_part(part) == part
    assert not asset_dir.exists()
    for update in ({"width": True}, {"name": "x\n"}, {"detail": "low"}, {"url": "https://x"}):
        with pytest.raises(assets.AssetError):
            assets.validate_part({**part, **update})
    assert json.loads(json.dumps(part)) == part


def test_source_name_does_not_define_identity():
    red = assets.ingest_image(png(color="red"), "same.png")
    blue = assets.ingest_image(png(color="blue"), "same.png")
    assert red["asset_id"] != blue["asset_id"]
    assert assets.resolve_image(red) == red


def test_orientation_is_applied_and_metadata_removed():
    output = io.BytesIO()
    source = Image.new("RGB", (30, 10), "red")
    exif = Image.Exif()
    exif[274] = 6
    source.save(output, format="JPEG", exif=exif)
    part = assets.ingest_image(output.getvalue())
    assert (part["width"], part["height"]) == (10, 30)
    data, _ = assets.read_image(part["asset_id"])
    with Image.open(io.BytesIO(data)) as image:
        assert not image.getexif()
