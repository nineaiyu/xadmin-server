# 交互式验证码底图

默认仍使用程序化底图：`captcha/backends/art.py` 内置 12 组浅色渐变配色，运行时生成
渐变 + 圆形/斜线纹理 + 噪点；旋转题额外绘制「天空 / 太阳 / 地平线 / 山」场景，用自然
景物提供明确正向语义。这样离线私有化部署不引入外部素材版权与体积负担，且每次出题都带
随机纹理。

内置 `photo/` 另有 24 张自绘 WebP（版本 3）：16 张分形/低多边形/植物纹理 + 8 张山景/
城市水面（`orientation=landscape`，供旋转题）；点选题会叠加浅色蒙层保证文字对比度。
图库为空或 `CAPTCHA_BACKGROUND_PACK=procedural` 时自动回退程序化底图。

## 可选图库包（CC0 / 自绘）

`captcha/assets.py` 读取本目录（或 `CAPTCHA_ASSET_DIR` 只读挂载目录）的
`manifest.json`；有文件时随机取图、`ImageOps.fit` 裁切并叠加噪点，无文件时回退程序化底图。

```json
{
  "pack_id": "builtin",
  "version": "1",
  "license": "CC0",
  "source": "https://example.com/cc0-pack",
  "files": [
    {
      "id": "landscape-01",
      "file": "photo/landscape-01.webp",
      "orientation": "landscape",
      "sha256": "<sha256>"
    }
  ]
}
```

约定：

1. 只收 **CC0 / OFL / Apache-2.0 / MIT / 自绘**，并在 `source` 写明来源；
2. 图片用 WebP，单张 ≤ 2MB，建议 640×360，剥离 EXIF/GPS，避免可识别人脸；
3. `file` 必须是相对路径，禁止 `..` / 绝对路径 / symlink；`sha256` 必填；
4. 旋转题优先使用 `orientation=landscape` 的图；图库为空即程序化兜底；
5. `CAPTCHA_BACKGROUND_PACK=procedural` 可强制关闭图库；
6. 新增/替换资产必须跑 `python scripts/check_captcha_assets.py`（CI 门禁同款）。

企业自定义图库：把只读目录挂到容器，设置 `CAPTCHA_ASSET_DIR=/path/to/pack`，目录内放
`manifest.json` 与图片即可。

## 外部图片 / AI 生成图导入（运维命令）

不开放匿名 HTTP 上传（版权、SSRF、恶意图片与供应链风险）。管理员在服务器执行：

```bash
python manage.py captcha_asset_import --dir ./cc0-images \
  --license CC0 --source https://example.com/cc0-pack --asset-dir /data/captcha-assets

# AI 生成图同样走这条链，许可声明为 self
python manage.py captcha_asset_import --dir ./ai-images \
  --license self --source "generated internally 2026-10" \
  --asset-dir /data/captcha-assets --pack-id ai-2026-10
```

命令会重新解码、裁切 640×360、转 WebP、剥离 EXIF、计算 sha256 并更新 manifest；
之后设置 `CAPTCHA_ASSET_DIR=/data/captcha-assets`（只读挂载）并跑
`python scripts/check_captcha_assets.py`。**不要直接开放上传接口**。
