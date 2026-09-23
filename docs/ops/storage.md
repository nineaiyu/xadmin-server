# 文件存储后端：声明式可插拔与搬迁

> 面向运维与部署：文件存储后端可在**运行期**从本地磁盘切到 S3 兼容对象存储
> （MinIO / 阿里云 OSS / AWS S3 等），无需改代码、无需重建镜像；含批量搬迁、
> 校验与回迁命令。

## 一、能力总览

| 项 | 说明 |
|----|------|
| 开关 | `FILE_STORAGE_BACKEND`：`local`（默认，本地磁盘）/ `s3`（对象存储）/ `mirror`（搬迁窗口双写：本地为主 + 对象存储尽力副本） |
| 生效方式 | SysConfig 声明式，**运行期热生效**（下一次文件操作即用新后端，无需重启） |
| 依赖 | `s3` / `mirror` 需可选依赖 `django-storages` + `boto3`（未启用零加载；未安装 / 配置不全自动回退本地并告警） |
| 密钥 | `FILE_S3_ACCESS_KEY` / `FILE_S3_SECRET_KEY` 经 signer 加密落库（凭据治理注册表，见 `common/core/credentials.py`） |
| 搬迁 | `manage.py storage_migrate`（幂等可断点续搬 / `--verify` 校验 / `--direction pull` 回迁）；不停服窗口走 `mirror`（见 §三点五） |
| 直连 | 受鉴权下载端点支持 `?direct=1` 返回**预签名短时效 URL**（仅 `s3` 后端；大文件不经服务端中转，鉴权与审计先于签发） |
| 观测 | `GET /api/common/api/health` 带 `storage_status` / `storage_time`（**不参与 status 判定**，对象存储抖动不让容器被判不健康） |

## 二、配置项

| SysConfig 键 | 默认 | 说明 |
|--------------|------|------|
| `FILE_STORAGE_BACKEND` | `local` | 存储后端：`local` / `s3` / `mirror`（双写窗口） |
| `FILE_S3_ENDPOINT` | 空 | 服务地址（如 `https://minio.example.com`；AWS 可留空用区域默认端点） |
| `FILE_S3_BUCKET` | 空 | 桶名（`backend=s3` 时必填，缺省回退本地并在日志告警） |
| `FILE_S3_ACCESS_KEY` | 空 | 访问密钥 ID（加密存储） |
| `FILE_S3_SECRET_KEY` | 空 | 访问密钥（加密存储） |
| `FILE_S3_REGION` | 空 | 区域（空 = 由 SDK / 端点决定） |
| `FILE_S3_CUSTOM_DOMAIN` | 空 | CDN / 公开访问域名；**非空时文件 URL 不签名**（需桶公开读） |
| `FILE_S3_ADDRESSING_STYLE` | 空 | `path` / `virtual`（MinIO 常需 `path`） |

配置写入方式二选一：

1. 管理界面「系统配置」（值级加密自动生效）；
2. `load_init_json` 种子 / 直接写 `SystemConfig` 表（密钥请走 `signer` 加密，或先用界面写一次）。

## 三、启用步骤（以 MinIO 为例）

```shell
# 1) 安装可选依赖（声明于 pyproject.toml [project.optional-dependencies].storage，默认不装）
#    开发环境（uv 快路径）：uv sync --extra storage
#    容器内（venv 的 pip 在 PATH 中；也可直接用容器内的 uv）：
docker exec xadmin-server sh -c "pip install django-storages boto3"
#    或：docker exec xadmin-server sh -c "uv pip install --python /data/py3 django-storages boto3"

# 2) 写配置（界面或 SQL 均可；下面为界面路径）
#    FILE_STORAGE_BACKEND = s3
#    FILE_S3_ENDPOINT    = http://minio:9000
#    FILE_S3_BUCKET      = xadmin
#    FILE_S3_ACCESS_KEY  = <access-key>
#    FILE_S3_SECRET_KEY  = <secret-key>
#    FILE_S3_ADDRESSING_STYLE = path

# 3) 搬迁存量文件（先看统计）
docker exec xadmin-server sh -c "cd /data/xadmin-server && python manage.py storage_migrate --dry-run"
docker exec xadmin-server sh -c "cd /data/xadmin-server && python manage.py storage_migrate"

# 4) 校验
docker exec xadmin-server sh -c "cd /data/xadmin-server && python manage.py storage_migrate --verify"
```

> 依赖需在每个运行文件链路的容器安装：`xadmin-server`（daphne）、`xadmin-celery-worker`、
> `xadmin-celery-heavy`。容器重建后需重装（或把依赖写进自定义镜像）。

**回滚**：把 `FILE_STORAGE_BACKEND` 改回 `local` 即回到本地磁盘（文件仍在；若搬迁后
才回滚，用 `storage_migrate --direction pull` 把对象存储上的文件拉回本地）。

## 三点五、不停服搬迁（mirror 双写窗口）

直切 `s3` 之前若存量文件较多，搬迁期间的新上传会落在「旧后端」，需要停机或有丢新文件的窗口。
`mirror` 模式消除该窗口（无停服、可随时回退）：

```shell
# 1) 切双写：新上传同时落本地（主）与对象存储（副本）；读 / 下载仍走本地，业务零感知
#    FILE_STORAGE_BACKEND = mirror

# 2) 补齐存量（幂等、可分批；只搬本地有而远端没有的）
python manage.py storage_migrate            # --limit N 分批观察
python manage.py storage_migrate --verify   # 校验目标完整性

# 3) 切主用：确认校验通过后，改为 s3 即完成搬迁（下一次文件操作生效，无需重启）
#    FILE_STORAGE_BACKEND = s3

# 回退（任一阶段）：改回 local 即可（本地副本始终完整）
```

语义要点：

- **读全走本地、写双写**：`mirror` 期本地始终是完整副本，任何时刻回退 `local` 都不丢文件；
- **副本尽力**：远端写入 / 删除失败只告警，不影响上传成功与本地删除（可用性优先）；
- **副本命名一致**：本地完成唯一命名后按同对象名复制（远端 `file_overwrite=True`），
  `storage_migrate --verify` 可直接按名比对。

## 四、storage_migrate 命令

| 参数 | 说明 |
|------|------|
| `--direction push`（默认） | 本地磁盘 → 对象存储 |
| `--direction pull` | 对象存储 → 本地磁盘（回迁 / 撤离对象存储） |
| `--dry-run` | 只统计将搬迁 / 将跳过的数量，不写入 |
| `--verify` | 只校验目标完整性（存在性 + 大小）；配合 `--md5` 追加逐文件 md5 比对 |
| `--overwrite` | 目标存在但大小不一致时覆盖（默认记为 `conflict` 不覆盖，避免冲掉目标端较新内容） |
| `--limit N` | 只处理前 N 个（分批观察；重复执行天然断点续搬） |

语义要点：

- **幂等**：目标已存在且大小一致 → 跳过（`skipped`）；中断后重跑即从断点继续；
- **默认不覆盖**：大小不一致记 `conflict` 并输出明细（需人工确认或 `--overwrite`）；
- **范围**：`UploadFile` 全部记录（含软删——回收站恢复 / 变更历史依赖物理文件）；
- **退出码**：出现 `failed` / `verify_failed` / `conflict` / `missing_source` 时非 0（可接 CI / 巡检）。

## 五、运行期行为与边界

| 场景 | 行为 |
|------|------|
| 上传 / 写入 | 统一走 storage API（`file_overwrite=False`：同名不覆盖） |
| 下载（受鉴权端点） | `storage_open()` 流式返回（本地 / 远端一致），不依赖本地绝对路径 |
| 在线预览（图片 / 文本 / Office） | 远端对象先落 `MEDIA_ROOT/storage_cache`（按最近使用保留，与预览缓存同任务清理），PIL / LibreOffice 在本地缓存上处理 |
| `/media/` 兜底直链 | 本地目录无文件时回落到 storage 读取（应用层代理）；生产建议配 `FILE_S3_CUSTOM_DOMAIN` 让文件 URL 直指 CDN |
| 缩略图（`ProcessedImageField`） | 仅本地后端生成 / 删除 `_1.jpg` 缩略图（远端后端跳过，URL 直接用对象地址） |
| 缓存清理 | `auto_clean_preview_cache` 周期任务同源清理 `storage_cache`（保留期 = `FILE_PREVIEW_CACHE_KEEP_DAYS`） |
| mirror 双写（搬迁窗口） | 新写入同时落本地与远端副本（副本失败只告警）；读 / URL / 本地路径全走本地（`storage_is_local()` 为真） |
| 预签名直连（`?direct=1`） | 仅 `s3` 后端且 boto3 可用时签发（默认 10 分钟）；本地 / mirror / 依赖缺失返回 `direct=false` 由调用方回退服务端中转；**签发前已完成鉴权与文件访问审计** |
| 依赖缺失 / 配置不全 | 回退本地并输出一条 WARNING（`storage backend fallback to local: ...`），文件链路不中断 |

## 六、排障

| 现象 | 排查 |
|------|------|
| 仍落本地磁盘 | 查 `FILE_STORAGE_BACKEND` 是否 `s3`；查容器日志有无 `fallback to local` 告警（依赖缺失 / bucket 未配） |
| 上传 500 | 依赖是否装齐（`storages` / `boto3`）；端点 / 桶 / 凭据是否正确；`addressing_style` 是否需要 `path`（MinIO） |
| 文件 URL 不可访问 | 私有桶走签名 URL（默认）；配了 `FILE_S3_CUSTOM_DOMAIN` 则需桶公开读 |
| health 里 `storage_status=false` | 看 `storage_time` 字段（错误原因）；本地后端 = MEDIA_ROOT 不可写；远端 = 网络 / 凭据 / 桶不可达 |
| 搬迁有 `conflict` | 目标端同名对象大小不同：确认后 `--overwrite`，或人工比对后处理 |
