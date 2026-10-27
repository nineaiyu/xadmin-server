#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""把外部图片导入为验证码资产包（只读目录 + manifest）。

这是**运维侧导入**，不是匿名 HTTP 上传：
- 仅管理员在服务器执行，必须声明许可与来源；
- 图片重新解码并转 WebP（剥离 EXIF），拒绝 SVG / 超大文件 / 路径穿越；
- 写入 ``CAPTCHA_ASSET_DIR``（或 --asset-dir）的 manifest，运行时只读加载。

用法::

    python manage.py captcha_asset_import --dir ./my-images \
        --license CC0 --source https://example.com/cc0-pack

    python manage.py captcha_asset_import --dir ./ai-images \
        --license self --source "generated internally" --pack-id ai-2026-10
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageOps

ALLOWED_LICENSES = {"CC0", "self", "OFL", "Apache-2.0", "MIT"}
ALLOWED_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png"}
MAX_BYTES = 10 * 1024 * 1024


class Command(BaseCommand):
    help = "导入验证码背景图到只读资产目录（管理员离线执行，不走 HTTP 上传）"

    def add_arguments(self, parser):
        parser.add_argument("--dir", required=True, help="待导入图片目录")
        parser.add_argument("--license", required=True, help="许可（CC0/self/OFL/Apache-2.0/MIT）")
        parser.add_argument("--source", required=True, help="来源 URL 或内部生成说明")
        parser.add_argument("--asset-dir", default="", help="目标只读资产目录（默认 CAPTCHA_ASSET_DIR）")
        parser.add_argument("--pack-id", default="custom", help="manifest pack_id，默认 custom")

    def handle(self, *args, **options):
        license_name = str(options["license"])
        if license_name not in ALLOWED_LICENSES:
            raise CommandError(f"license must be one of {sorted(ALLOWED_LICENSES)}")
        source = str(options["source"]).strip()
        if not source:
            raise CommandError("source is required")
        source_dir = Path(options["dir"]).expanduser().resolve()
        if not source_dir.is_dir():
            raise CommandError(f"source dir not found: {source_dir}")
        asset_dir = str(options["asset_dir"] or getattr(settings, "CAPTCHA_ASSET_DIR", "") or "").strip()
        if not asset_dir:
            raise CommandError("set CAPTCHA_ASSET_DIR or pass --asset-dir (writable read-only asset dir)")
        target = Path(asset_dir).expanduser().resolve()
        photo_dir = target / "photo"
        photo_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = target / "manifest.json"
        manifest = self._load_manifest(manifest_path, options["pack_id"])
        existing = {str(item.get("id")): item for item in manifest.get("files") or [] if isinstance(item, dict)}
        imported = 0
        for path in sorted(source_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if path.stat().st_size > MAX_BYTES:
                self.stderr.write(f"skip oversized: {path.name}")
                continue
            try:
                with Image.open(path) as image:
                    if image.width < 160 or image.height < 90 or image.width > 4096 or image.height > 4096:
                        self.stderr.write(f"skip bad dimensions: {path.name}")
                        continue
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image = ImageOps.fit(image, (640, 360), method=Image.Resampling.BICUBIC)
                    digest = hashlib.sha256(image.tobytes()).hexdigest()[:12]
                    name = f"{path.stem.lower().replace(' ', '-')}-{digest}.webp"
                    out = photo_dir / name
                    image.save(out, "WEBP", quality=86, method=6)
            except (OSError, ValueError) as exc:
                self.stderr.write(f"skip unreadable {path.name}: {exc}")
                continue
            item = {
                "id": out.stem,
                "file": f"photo/{out.name}",
                "orientation": "landscape",
                "width": 640,
                "height": 360,
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            }
            existing[item["id"]] = item
            imported += 1
        manifest.update(
            {
                "pack_id": options["pack_id"],
                "version": str(int(manifest.get("version") or 0) + 1),
                "license": license_name,
                "source": source,
                "files": sorted(existing.values(), key=lambda item: str(item.get("id") or "")),
            }
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"imported {imported} image(s) into {target}"))

    @staticmethod
    def _load_manifest(path: Path, pack_id: str) -> dict:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                pass
        return {"pack_id": pack_id, "version": "0", "license": "", "source": "", "files": []}
