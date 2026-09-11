# ADR-013：Office 在线预览选型（LibreOffice headless 转 PDF）

- 状态：已接受
- 日期：2026-09-11
- 关联：排期文档《剩余任务排期-2026.09-2027.02》O1–O3（11 月候选池②）；
  N3 五期「文件在线预览与缩略图」（本 ADR 落地其显式排除项 Office 预览）；
  ADR-005（Redis 拆分）、heavy 队列（`CELERY_TASK_ROUTES`）

## 背景

文件中心已支持图片 / PDF / 文本在线预览（`system/utils/preview.py`，
`GET /api/system/file/{pk}/preview`），Office 文档（docx/xlsx/pptx 等）当时被
显式排除。业务侧需要"预览不下载"的办公文档能力——既要避免敏感文档被随意下载，
也要降低用户为"看一眼"而安装本地 Office 的成本。

三个候选方案：

| 方案 | 形态 | 部署成本 | 格式还原度 | 许可/依赖 |
|------|------|----------|-----------|-----------|
| **LibreOffice headless** | 本机进程转换（CLI） | 低：容器内 apt 安装即可 | 中高（常规文档良好，复杂排版有差异） | MPL/LGPL，无外部服务 |
| OnlyOffice Document Server | 独立服务 + 前端 SDK | 高：额外容器/域名/回调，需 JWT 互信 | 高（接近原生 Office） | AGPL（社区版），协作能力是主要卖点 |
| WPS 开放平台 | 第三方云 API | 低（调用侧），但数据出域 | 高 | 商业授权，文档需上传第三方 |

## 决策

**采用 LibreOffice headless 转 PDF，自托管、无外部依赖。**

实现要点（O2）：

1. **转换链路**：`soffice --headless --norestore --invisible
   -env:UserInstallation=<临时 profile> --convert-to pdf --outdir <tmp> <src>`；
   独立 profile 目录规避并发 profile 锁冲突；`subprocess.run(timeout=...)` 硬超时。
2. **队列归属**：转换以 celery 任务
   `system.tasks.convert_office_preview_task` 投递 **heavy 队列**
   （`CELERY_TASK_ROUTES` 按任务名路由，与大数据量导出同池），严禁占用默认队列。
3. **请求/产物协议**：预览请求命中未转换的 Office 文件时派发任务并短等
   `FILE_OFFICE_WAIT_SECONDS`（默认 8s）；产物就绪即 inline 返回 PDF；
   等待窗口结束仍在转换 → **HTTP 425 + 业务码 1006**，前端轮询重试
   （`PreviewDrawer` 最多 8 次 × 2s）；转换失败（锁已释放且无产物）或环境不可用
   → 降级 1005「不支持预览」，**不影响下载**。
4. **缓存与回收**：产物落 `preview_cache/<pk>/office.pdf`，与图片缓存同目录 →
   复用既有三条回收路径（源文件删除联动 / 孤儿清理 / 保留期清理），不引入新表。
5. **白名单与限额**：`OFFICE_EXTENSIONS` + `OFFICE_MIME_TYPES`（csv 等文本优先走文本
   预览）；`FILE_OFFICE_MAX_BYTES`（默认 20MB）超限不转换（保护转换进程）；
   `FILE_OFFICE_PREVIEW_ENABLED` 总开关；`FILE_OFFICE_SOFFICE_BIN` 支持自定义路径。
6. **鉴权不变**：沿用 `preview:SystemUploadFile` 菜单码 + 数据权限收口，
   产物不落 /media 直链。

## 后果

- 正面：自托管、无数据出域（合规友好）；部署只多一个 apt 包；与既有预览链路
  （类型分派 / 缓存回收 / 鉴权）零冲突；转换不阻塞默认队列。
- 负面：转换依赖本机进程与字体，**复杂排版/特殊字体存在还原差异**（部署文档要求
  安装常用字体缓解）；首次预览有秒级等待（后续命中缓存）；CPU 密集，heavy 池
  concurrency 需按机器规格评估。
- 中性：未安装 LibreOffice 的环境自动降级为「不支持预览」（开关默认开、
  可用性运行时探测），因此老部署升级不会因缺依赖而报错；OnlyOffice 的协作编辑、
  WPS 的云转换能力不在本期范围，如未来需要协作编辑需重新立项（独立 ADR）。

## 测试与验收

- 单测/集成：类型分派（含 csv 文本优先）、可用性降级（开关/转换器缺失/超限）、
  缓存命中、eager 转换链路、425 转换中、失败降级、源文件删除联动清理；
- 真实转换：`requires_converter` 标记，docx（OOXML 最小样本）/xlsx 转 PDF 断言
  `%PDF-` 头；无 LibreOffice 环境自动跳过（CI 默认不装，本地/带依赖环境跑真实链路）；
- E2E：docx 上传 → 预览抽屉「转换中」→ 轮询 → PDF 内嵌（无 LibreOffice 环境跳过）。
