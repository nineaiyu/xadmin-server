# -*- coding: utf-8 -*-
"""资产导入命令：重编码为 WebP、剥离元数据、写 manifest。"""

import json
from io import StringIO

from django.core.management import call_command
from PIL import Image


def test_import_command_writes_manifest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (800, 450), (40, 80, 120)).save(source / "sample.png")
    target = tmp_path / "assets"

    call_command(
        "captcha_asset_import",
        dir=str(source),
        license="CC0",
        source="self",
        asset_dir=str(target),
        pack_id="custom",
        stdout=StringIO(),
    )

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pack_id"] == "custom"
    assert manifest["license"] == "CC0"
    assert len(manifest["files"]) == 1
    item = manifest["files"][0]
    assert item["file"].endswith(".webp")
    assert (target / item["file"]).is_file()
    assert item["sha256"]
