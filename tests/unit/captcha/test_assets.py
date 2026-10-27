# -*- coding: utf-8 -*-
"""背景资产 manifest：空包回退程序化；自定义只读目录可加载并裁切。"""

import hashlib
import json

from PIL import Image

from captcha.assets import background_assets, load_background, load_manifest, select_background


def _write_pack(tmp_path):
    image_path = tmp_path / "photo.png"
    Image.new("RGB", (80, 40), (20, 30, 40)).save(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    manifest = {
        "pack_id": "custom",
        "version": "1",
        "license": "CC0",
        "source": "self",
        "files": [{"id": "p1", "file": "photo.png", "orientation": "landscape", "sha256": digest}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_builtin_pack_has_self_drawn_backgrounds():
    manifest = load_manifest()
    assert manifest["pack_id"] == "builtin"
    assert len(manifest["files"]) >= 24
    assets = background_assets()
    assert len(assets) >= 24
    assert any(asset.orientation == "landscape" for asset in assets)
    image, asset_id = select_background((64, 64))
    assert image is not None and image.size == (64, 64)
    assert asset_id
    assert load_background((64, 64)) is not None


def test_custom_asset_dir_loads_and_crops(settings, tmp_path):
    _write_pack(tmp_path)
    settings.CAPTCHA_ASSET_DIR = str(tmp_path)
    # 自定义目录本身就是选中的包，不要求 pack_id 与 CAPTCHA_BACKGROUND_PACK 同名
    settings.CAPTCHA_BACKGROUND_PACK = "builtin"
    assets = background_assets()
    assert len(assets) == 1
    assert assets[0].orientation == "landscape"
    image = load_background((64, 64))
    assert image is not None
    assert image.size == (64, 64)


def test_path_traversal_is_ignored(settings, tmp_path):
    _write_pack(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["file"] = "../outside.png"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    settings.CAPTCHA_ASSET_DIR = str(tmp_path)
    settings.CAPTCHA_BACKGROUND_PACK = "builtin"
    assert background_assets() == []
