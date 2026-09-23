# ADR-053：JumpServer 对标批四「触发制与工程改造」

| 项目 | 内容 |
|------|------|
| 状态 | 已实施（2026-09-23） |
| 关联 | [JumpServer 对标完善方案](../plans/JumpServer对标完善方案-2026.09.md) §四 批四 · [ADR-050](ADR-050-jumpserver-batch1-security-and-decision.md)（批一）· [ADR-051](ADR-051-jumpserver-batch2-ai-platform-and-ux.md)（批二）· [ADR-052](ADR-052-jumpserver-batch3-usability-and-security.md)（批三） |
| 影响面 | 后端：依赖工程化（pyproject + uv.lock + 导出产物）、`log_archive` 命令与清理链路改造、`RelationCountMixin`、`ControlledLookupFilterBackend`、`?fields=` 子集、用户邀请与有效期（+1 迁移、+1 权限点、+2 SysConfig 键）、`system/views/auth/invite.py`；前端：用户页邀请入口与状态列、邀请激活页（新）、仪表盘/大屏图片导出（新 utils）、F-12 跳转接线 |

## 1. 决策摘要

按方案 §四 批四清单交付**非触发制项**（E-1 / P-5 / F-11 剩余 / F-12 / F-13 / U-5），
并登记**触发制项未命中不实施**（P-4 文件存储后端、F-10 标准 OIDC）：

1. **触发未命中即不动**（延续 R 线纪律）：P-4 的触发条件（多实例部署 / 文件容量超阈值 /
   共享存储诉求）与 F-10 的触发条件（企业 IdP 对接诉求）均未出现，本轮不实施、不预埋半成品；
2. **工程改造零行为变化**：E-1 保持「安装器 / Docker / CI 走 requirements 产物」的既有路径不变，
   uv 只是新的快路径与可复现锁；P-5 保持「物理删除」的最终语义，只在删除前插入归档与水位校验；
3. **声明式优先、显式开关**：F-12 用序列化器声明计数表达式（未声明视图零变化）；
   F-13 受控 lookup 需视图显式 `controlled_lookup = True` 且字段面受 filterset 白名单约束；
4. **fail-closed 与可读文案**：F-13 无权字段过滤 400（不落空集侧信道）、非法值 400（不落 500）；
   F-11 未配置邮件渠道时邀请 fail-closed 提示；P-5 归档失败则跳过清理（保数据优先）。

## 2. 交付清单

| 编号 | 能力 | 关键实现 |
|------|------|----------|
| E-1 | 依赖工程化（pyproject + uv） | `pyproject.toml` 为**唯一事实源**（运行依赖 + `dev` 组）；`uv.lock` 入库（154 包解析锁）；`requirements*.txt` 改为 `uv export` 产物（含传递依赖 + 平台 marker，`--no-annotate` 精简）；三方一致性守护 `tests/unit/test_dependency_manifest.py`（纯解析，不依赖 uv 与网络；本机有 uv ≥ 0.12 时额外校验「产物与 `uv export --frozen` 逐行一致」）；README 增 uv 快路径与导出命令；CI / Docker / 安装器路径零改动 |
| P-5 | 审计归档与冷热分层 | `system/utils/log_archive.py`：整月归档 `<model>-<YYYY-MM>.jsonl.gz` + `.sha256` + `.manifest.json`（行数 / 时间范围 / 校验和，幂等跳过）；`manage.py log_archive`（归档 / `--dry-run` / `--list` / `--verify` / `--restore-range` 离线流式查询 / `--prune`）；**归档水位驱动清理**——`auto_clean_operation_job` 改为「先归档后清理」，删除边界 = 已归档水位 ∩ 保留窗口（成功 / 错误分层语义不变），归档失败抛错并跳过清理；归档目录 `LOG_ARCHIVE_DIR`（默认 `DATA_DIR/log_archive`，env 可覆盖）；文档 `docs/ops/log-archive.md` |
| F-12 | 列表关联计数声明式 | `RelationCountMixin`（按 `serializer_class.relation_count_fields` 声明 `Count(...)`，注解名 = 字段名；仅 `auto_prefetch_actions` 生效；`annotate` 后显式补 `order_by`）；试点：角色 `user_count`（新增）、数据集 `report_count`（新增）、部门 `user_count`（存量 `AnnotateUserCountMixin` 收敛为别名）；与 F-2 影响面同源（角色→用户、数据集→报表）；前端角色 / 数据集计数列可点击跳转（角色 → 用户列表 `?role=`、数据集 → 报表页 `?dataset=`） |
| F-13 | API 查询能力（受控 lookup + 字段子集） | `ControlledLookupFilterBackend`：字段面 = `filterset_class` 已声明 `field_name` ∪ `controlled_lookup_fields` ∪ `pk`；lookup 限定 `exact/icontains/startswith/in/gte/lte/isnull/ne`（`ne` 取反；M2M 仅 `exact/in/ne`；**禁跨关系**）；值按模型字段 `to_python` 转换（布尔容错 true/false；失败 400）；条件数上限 20；**字段可见性 fail-closed**（非超管必须命中 `request.fields` 白名单，否则 400——不给过滤侧信道）；`?fields=` 字段子集在 `BaseViewSet.get_serializer` 注入（仅 GET，与声明字段 / 字段权限 / 应用授权求交，上限 100）；试点：用户列表 + 操作日志 |
| F-11 | 邀请开户 + 账号有效期 | `UserInfo.date_expired` / `invite_status` / `invited_time`（迁移 `system/0011`）；`system/utils/user_invite.py`：邀请 = 待激活 + **不可用密码**（登录被拒）+ 一次性令牌（`TokenTempCache`，`scene=user_invite`，TTL 72h）+ 邮件链接（`WEB_SITE_URL` 或请求推导）；激活端点 `auth/invite/{validate,accept}`（匿名、复用重置密码限流、密码强度 / 泄露 / 历史同口径）；`POST /user/{pk}/invite`（权限点 `invite:SystemUser`）；账号有效期 = `login_success` 拦截 + 每日 08:00 任务（到期前 N 天提醒（`ACCOUNT_EXPIRY_REMIND_DAYS`，同日去重）+ 到期自动停用并通知）；前端：用户页「邀请激活」行操作 + 状态列、邀请激活页（`/invite/accept`，匿名白名单） |
| U-5 | 图表 / 大屏导出图片（零新依赖） | `src/utils/imageExport.ts`：ECharts（SVGRenderer）`getDataURL` → SVG → **转 PNG**（Image + canvas），转换失败回退 SVG 原图；ZIP **store 模式手写实现**（CRC32 + local/central/EOCD，PNG 已压缩无需 deflate），单次下载规避浏览器连续下载拦截；仪表盘卡片工具栏「导出图片」；大屏「导出当前屏」逐卡打包 ZIP（指标卡跳过并汇总提示）；`crc32` / `buildZipStore` / `safeFileName` 有 vitest 单测 |

## 3. 关键设计决策

### 3.1 E-1：uv 双轨与「产物未手改」的守护层次

- **单源**：依赖只写 `pyproject.toml`；产物与 lock 均由 uv 生成（CI 断言三方一致）；
- **CI 不装 uv**：CI 的守护是纯解析（pyproject ↔ 产物 ↔ lock），不引入 uv 二进制依赖；
  「产物是否被手改」由本机 uv（≥ 0.12）在单测中额外校验（`--frozen` 导出逐行比对）；
- **产物形态**：导出**完整依赖闭包**（含传递依赖与平台 marker）而非仅直接依赖——
  lock 可复现的价值落在安装侧（pip 无需再解析），`requirements` 与 lock 语义等价。

### 3.2 P-5：为什么是「归档水位」而不是「按天删除」

按天删除 + 按月归档会有一个窗口：边界月的部分行已超期被删、但该月尚未「整月可归档」。
**水位驱动**把删除条件收紧为「该行所在月已完整归档」，代价是最多多留一个月（保留期是下限），
换得「删必已归档」的不变式与可恢复性；水位从「系统最早数据」起连续校验（洞 / 未归档月一律不越过），
归档目录被搬迁或清空时水位回退（安全方向）。

### 3.3 F-13：字段面不扩张、只放开 lookup

白名单取「filterset 已声明字段」，**不新增可过滤字段面**——受控 lookup 的价值是省掉
「每加一个 lookups 组合就改后端」，而不是让任何字段可被过滤；字段可见性复用序列化器同口径
（超管全量 / 其余 `request.fields`），保证「过滤结果 ⊆ 可见数据」，不留探测侧信道。

### 3.4 F-11：邀请用「不可用密码」而非 `is_active=False`

登录被拒由 Django 认证链路天然保证（unusable password），且**不与「停用」语义混淆**：
安全巡检（F-6）、到期停用（F-11）、在线会话等 `is_active` 口径不受影响；
激活即 `set_password` + `invite_status=accepted` + `record_password_hash`（顺带清强制改密标记）。
令牌不显式删除（保留至自然过期），复用返回「已激活」可读提示，安全由状态机保证。

### 3.5 U-5：为什么手写 ZIP 而不是逐卡多次下载

Chromium 对无用户手势的连续多次下载会拦截（且不可靠），大屏导出必须「一次下载」；
浏览器无原生 zip 打包 API，引入 jszip 违背「零新依赖」，故按 ZIP 规范 store 模式手写
（约 120 行，含 CRC32 与结构单测）。

## 4. 边界与遗留

- **F-11**：邀请激活页密码按明文接收（生产应经 HTTPS；与注册 / 忘记密码的 AES 链路差异已登记）；
  「创建用户时一步邀请」未做（先创建再点邀请，两步）；登录日志（UserLoginLog）**未启用**自动归档 / 清理
  （`--model login` 手动归档可用），触发条件 = 登录日志表体积成为运维负担；
- **F-12**：存量 dict（`children_count`）/ 审批（`node_count`）两处「注解名与字段名不一致」的手写实现未迁移
  （与声明式口径等价，按需迁移）；「计数可点击」当前仅角色 / 数据集两处接线；
- **F-13**：前端「高级筛选」未接入（方案标注「按需」，登记为按需）；
- **P-5**：归档文件纳入异地副本同步为运维动作（`LOG_ARCHIVE_DIR` 指向同步目录）；
  冷归档恢复演练已具备 `--verify` + `--restore-range` 工具面，**待进季度演练窗口执行**；
- **E-1**：uv 为快路径，pip 路径保留；「requirements 全部由 uv 生成」已生效，
  后续新增运行依赖须改 `pyproject.toml` 后重新导出（守护测试会拦手改）。

## 5. 触发制项登记（本轮未命中，不实施）

| 项 | 触发条件 | 本轮结论 |
|----|----------|----------|
| P-4 文件存储后端可插拔 | 多实例部署 / 文件容量超阈值 / 共享存储诉求 | 未命中（单机部署 + 本地盘容量充足）→ 不实施，条件命中后另立窗口 |
| F-10 标准 OIDC 单点登录 | 企业 IdP 对接诉求（Keycloak / Auth0 / Entra ID） | 未命中（既有 OAuth2 provider 覆盖现网）→ 不实施 |

## 6. 验证

- **后端**：pytest 全量 exit 0（新增依赖清单 5 / 归档 13 / 关联计数 7 / 查询能力 19 / 邀请与有效期 13 例）+
  ruff / 行数 / 跨 app / 缓存键 / `makemigrations --check` / 文档门禁（facts / index / paths / tutorial）全绿；
  权限点扫描（`test_permission_seed_coverage`）无缺口（`invite:SystemUser` 已入种子）；
- **前端**：typecheck / typecheck:strict / eslint / prettier / stylelint / vitest 324（含导图工具 7 例）/
  `check:i18n` / contract / build + 体积门禁 全绿；
- **E2E**：新 spec `e2e/batch4.e2e.ts`（F-12 计数跳转 / F-11 邀请状态 / U-5 导出下载）双浏览器 + 回归
  `system-pages` / `permissions` / `dashboard` 共 48 passed。

## 7. 运维步骤

1. `load_init_json` 重灌种子（新增权限点 `invite:SystemUser` 与 2 个 SysConfig 键）+ `compilemessages`；
2. `migrate`（`system/0011`：用户表三字段）+ 重启 daphne / celery（worker + beat，beat 需重新注册周期任务）；
3. 归档目录按需设置 `LOG_ARCHIVE_DIR`（默认 `DATA_DIR/log_archive`，不要放备份卷根目录——
   备份脚本 `prune_local` 会按 `*.sql.gz / *.media.tar.gz / *.sha256` 后缀清理存量文件）；
4. 可选：`uv` 快路径开发环境（`uv sync --all-groups`），CI / 镜像保持 pip 路径。
