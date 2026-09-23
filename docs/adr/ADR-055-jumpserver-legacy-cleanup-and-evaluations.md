# ADR-055：JumpServer 对标遗留零余量收口与评估出口评估

| 项目 | 内容 |
|------|------|
| 状态 | 已实施（2026-09-23） |
| 关联 | [JumpServer 对标完善方案](../plans/JumpServer对标完善方案-2026.09.md) §四 评估出口（不排期）· [ADR-050](ADR-050-jumpserver-batch1-security-and-decision.md)（批一）· [ADR-051](ADR-051-jumpserver-batch2-ai-platform-and-ux.md)（批二遗留）· [ADR-052](ADR-052-jumpserver-batch3-usability-and-security.md)（批三遗留）· [ADR-053](ADR-053-jumpserver-batch4-triggered-and-engineering.md)（批四遗留）· [ADR-054](ADR-054-remaining-u3-storage-oidc-and-cleanup.md)（剩余项收口，本轮接续） |
| 影响面 | 后端：`system/serializers/user.py` + `system/views/admin/user.py`（创建即邀请）、`AiUsageRecord.track` + `system/utils/ai_usage.py`（双轨对照）、`system/utils/task_progress.py`（新）+ `ExportRecord.stage`/`ImportRecord.stage` + 三链路接入（统一进度）、`common/storage/`（预签名 + mirror 双写）、`pyproject.toml` + `uv.lock`（依赖显式化 + storage extras）、`system/views/admin/file.py`（受控 lookup 扩页）、`common/celery/utils.py`（注释清账）；前端：用户新建表单「邀请激活」开关、AI 配置页 vision 探测与双轨对照展示、任务中心 stage 列、文件中心高级筛选；迁移：`system/0012_task_progress_stage`、`system/0013_ai_usage_track` |

## 1. 决策摘要

把四个 ADR（050~054）与方案 §四「评估出口（不排期）」中**仍未闭环的全部条目**收口——能实施的技术遗留全部实施，
属于触发制 / 重依赖红线的评估出口**逐项完成评估并落结论**（不再悬空）：

1. **技术遗留清零**：F-11 创建即邀请、AI-2 双轨对照统计、AI-1 vision 探测入口、P-2 统一进度助手 + 阶段描述、
   P-4 预签名直连与搬迁窗口双写（mirror）、E-1 依赖显式声明 + storage extras、F-13 高级筛选扩页；
2. **评估出口逐项结案**：独立 AI worker / 审计外部后端 / 服务端无头 PDF / Vault·KMS / ClamAV / CAS·SAML /
   prompt-JSON 下线——各自给出**结论 + 依据 + 触发条件复核**（§4），不再以「未评估」形态悬挂；
3. **红线不变**：重依赖（无头浏览器 / ES / Vault / ClamAV 侧车）仍不主动引入，本轮不因「清账」而破例；
4. **边界显式登记**：无跳转目标的计数（字典子项 / 流程节点）与预签名上传直传等**明确不做**的项写清理由（§5），
   避免下一轮被重复当作遗留。

## 2. 交付清单（技术遗留）

| 编号 | 能力 | 关键实现 |
|------|------|----------|
| F-11 遗留 | 创建即邀请（一步开户） | `UserSerializer.invite`（write_only 布尔；邀请模式放开 password 必填并忽略提交值）；`create()` 置不可用密码（由被邀请人自行设置）；`UserViewSet.create` 前置 fail-closed 校验（**邮件渠道已配置** + **邀请权限点 `invite:SystemUser`**，`user_has_permission` 与权限链同源）+ `perform_create` 复用 `send_invite`；前端用户新建表单「邀请激活」开关（`addOrEditOptions.columns.invite`）+ 密码动态校验（`inviteMode` ref）+ `beforeSubmit` 邀请模式不提交密码；守护测试 5 例 |
| AI-2 遗留 | 双轨对照统计 | `AiUsageRecord.track`（`prompt` / `native`，仅动作草稿链路写入）；`record_usage(track=)` + `tracked_chat` / `tracked_chat_tools` / `tracked_chat_stream` 透传；调用点标注轨道（`native_draft_result` / actions.py / message/ai.py）；`usage_summary` 新增 `by_track`（calls / failed / **success_rate**）；前端 AI 配置页用量卡片展示「草稿链路：原生轨道 / prompt-JSON 轨道 · 次数 · 成功率」；守护测试 2 例 |
| AI-1 遗留 | vision 探测入口 | 前端新增「探测（含多模态）」操作（`aiProfileApi.probe(pk, { vision: true })`），**保留原三项探测为默认**（避免无多模态模型上的无谓等待，ADR-051 §遗留口径不变）；能力画像列**按需**展示 vision tag（有探测结果才出现 → 默认形态零变化）；提示文案含 vision 结果 |
| P-2 遗留 | 统一进度助手 + 阶段描述 | 新 `system/utils/task_progress.py::update_progress`（kind: export / import / report；导出·报表落库、导入运行期走缓存通道**（大事务内写库对下载中心不可见）**、中间里程碑统一协作式取消检查、终态 100 不检查）；`ExportRecord.stage` / `ImportRecord.stage`（迁移 0012）+ 阶段文案（统计行数 / 渲染内容 / 查询数据集 / 渲染报表文件）；**报表任务补中间里程碑**（20 → 80 → 100，此前只有终态）；导出 `_save_progress` 与导入调用点改走统一入口；任务中心统一视图与列表序列化器带出 `stage`，前端进度列下方展示；守护测试 16 例 |
| P-4 遗留 | 预签名直连 + 搬迁窗口双写 | `storage_presigned_url(name, expires=600, download_filename=)`（仅 `s3` 后端；boto3 可选依赖缺失返回 None）；受鉴权下载端点 `?direct=1` 返回短时效签名 URL（**鉴权与文件访问审计先于签发**，本地 / mirror 恒回退中转）；`MirrorStorage` + `FILE_STORAGE_BACKEND=mirror`：**本地为主 + 对象存储尽力副本**（新写入双写、读 / URL / 本地路径全走本地、副本失败只告警）→ 不停服搬迁窗口（切 mirror → `storage_migrate` 补齐 → 切 s3）；`docs/ops/storage.md` 补 §三点五与行为表；守护测试 7 例（双写 / 副本失败不阻断 / 回退 / 预签名参数与缺失依赖） |
| E-1 遗留 | 依赖显式声明 + 可选依赖分组 | `pyproject.toml` 显式声明 `pyjwt` / `cryptography` / `cbor2`（此前仅为传递依赖，源码直接 import）；新增 `[project.optional-dependencies].storage`（`django-storages` / `boto3`，**默认不装**）；`uv.lock` 刷新（154 → 159 包）；守护测试新增「extras 必须在 lock 解析且**不得进入默认运行产物**」；pyproject 导出命令注释修正为与 README / 守护测试同口径的 `uv export`；README 与 `docs/ops/storage.md` 补 `uv sync --extra storage` 快路径 |
| F-13 扩页 | 高级筛选第三页 | 文件中心 `UploadFileViewSet` 声明 `controlled_lookup` + `ControlledLookupFilterBackend`（字段面 = filterset 已声明字段，字段可见性 fail-closed）；前端文件中心开启 `advanced-filter`（用户管理 / 操作日志 / 文件中心三页 opt-in） |
| 清账 | 注释与词条 | `common/celery/utils.py` 的历史 Todo 注释改为**说明性注释**（为何不做任务名硬校验：种子可先于 autodetect 导入，硬校验误报；无效任务名由 beat 触发时 NotConfigured 报错可见）；后端 zh 语言包补齐本轮新增词条（Invite activation / 进度阶段 / 阶段文案等 6 条） |
| 清账 | i18n 漏翻收口与 `msgfmt` 修复 | 后端 zh 语言包补**漏翻的用户可见文案 150 条**（批二~批四新增的 label / 提示 / 错误文案，此前中文界面静默回退英文——门禁只校验 po 内部一致，抓不到「代码有文案而 po 未收录」）；修复既有 `msgfmt` fatal：原生工具调用 prompt 的中文译文里字面 JSON 大括号未按 brace-format 转义为 `{{ }}`，导致 zh `.mo` **长期编译失败**（`compilemessages` 现全绿）；**边界**：面向 LLM 的 prompt 模板与工具描述（约 113 条长文本）不补 zh 译文（非界面文案，英文更稳，见 §5） |
| 收口 | Passkey 浏览器侧 E2E | 新增 `e2e/passkey.e2e.ts`：CDP 虚拟认证器（`WebAuthn.enable` + `addVirtualAuthenticator`）走完 `navigator.credentials.create` → 服务端验签 → 落库 → 列表回显 → 删除全链路（chromium 1 passed，webkit 按设计 skip）——补齐 ADR-052 §5「真实认证器仅单测覆盖」的浏览器侧缺口 |

## 3. 关键设计决策

### 3.1 创建即邀请：为什么在视图层做邮件渠道与权限双前置

- **fail-closed 不产生死号**：邀请模式账号密码不可用，若邮件渠道未配置则用户既收不到链接也无法登录——
  故渠道校验放在创建**之前**（返回 1001），而不是创建后补发失败；
- **权限口径不扩张**：创建即邀请 = 创建 + 邀请两个动作的合并，需同时具备 `create:SystemUser`（路由权限链）
  与 `invite:SystemUser`（`user_has_permission` 按权限点 path 判定，与运行时访问控制同源）——只给创建权限的
  角色不会因带 `invite=true` 而获得发信能力（403 且不创建）；
- **编辑链路零变化**：`invite` 为 write_only 且仅新建展示；已激活 / 普通账号的 `invite_status` 仍由
  invite action 与激活端点维护，建号路径不写该字段（避免与「重发邀请重置为待激活」语义混淆）。

### 3.2 统一进度助手：为什么导出落库、导入走缓存

- 导入是「外层一个大事务 + 每行 savepoint」的原子语义（失败率超限回滚全部成功行），事务提交前
  **其他连接读不到本事务里的进度**（进度条会从 0 直接跳到 100）——因此导入运行期继续走缓存通道
  （`import_progress`，跨连接可见），终态与阶段描述落库；
- 导出 / 报表无此约束，统一落库（`progress` + `stage` + `updated_time` 一次 `update()`，不触发模型 save 链）；
- **取消检查只在中间里程碑**：终态 100 检查会把「已完成的导出」翻成取消（导出完成与用户点取消可能同帧发生），
  故 100 跳过检查——与 `_export.py` 原注释口径一致。

### 3.3 mirror 双写：读走本地是刻意的

- 搬迁期最有价值的性质是**任何时刻可回退**：本地为主存储 → 本地始终完整 → 改回 `local` 不丢文件；
- 远端副本写入失败只告警（对象存储抖动不应阻断上传），副本按同对象名复制（`file_overwrite=True`）
  以便 `storage_migrate --verify` 直接按名比对；
- `storage_is_local()` 对 mirror 判真：缩略图生成、`storage_local_path` 等本地专属逻辑行为一致（业务零感知）。

### 3.4 预签名只用于下载方向

上传直传（前端 → presigned PUT → 回调登记）会绕过既有上传链路的 **md5 去重 / 图片重编码 / 配额与
扩展名策略 / 访问审计**，收益（省一次服务端中转）低于复杂度与安全成本，**明确不做**（§5 边界）；
下载方向的无状态读放大（大文件中转）是真瓶颈，故只在该方向提供，且签发前完成鉴权与审计。

## 4. 评估出口评估结论（逐项结案）

| 项 | 结论 | 依据 | 触发条件复核 |
|----|------|------|--------------|
| 独立 AI worker / 网关（Kael 形态） | **不实施** | 单实例 compose 部署；AI 并发由 `AI_QUOTA_MAX_CONCURRENT_STREAMS` 信号量护栏 + 异步面（celery）已分离；分离网关的运维成本 > 收益（ADR-015 §2 边界） | 多实例部署 **或** AI 并发成为 daphne worker 瓶颈时重开 |
| 审计外部后端（ES / ClickHouse） | **不实施** | P-5 已具备「整月归档 + sha256 清单 + 离线恢复查询」（`log_archive --restore-range`）与保留期清理，检索诉求由索引 + 归档覆盖；ES 属重依赖（红线） | `OperationLog` > 2000 万行 **或** 审计检索 P95 > 1s（监测：行数走 `pg_stat_user_tables`，P95 走慢请求告警口径） |
| 服务端无头浏览器 PDF | **不实施** | 重依赖（镜像 +300MB 级，红线）；图表 / 大屏导出已由 U-5 前端轻方案（SVG→PNG + ZIP）覆盖 | 正式 PDF 需求明确且接受镜像增重时重开 |
| Vault / KMS 外部密钥管理 | **不实施** | P-3 凭据治理已覆盖：敏感键注册表 + 值级加密（AESCipherV3）+ 轮换命令 + 明文守护入 CI（「密钥在哪 / 是否加密 / 多久轮换」可答） | 合规要求独立密钥托管时重开 |
| ClamAV 病毒扫描（侧车） | **不实施** | 上传已有扩展名策略（fail-closed）+ 图片魔数校验 + 重编码；侧车属重依赖 | 合规要求病毒扫描时评估侧车方案 |
| CAS / SAML 单点登录 | **不实施**（CAS / SAML 维持评估出口） | 标准协议诉求已由 F-10 OIDC（discovery + id_token 验签 + 组角色映射）覆盖；SAML 需 XMLDSig 验签链（xmlsec 依赖，重）；CAS 单独实现收益低于维护面 | 真实企业 IdP 仅支持 CAS / SAML 时重开（优先 CAS，可复用既有 OAuth flavor 框架） |
| prompt-JSON 轨道下线 | **不下线**（评估口径已就绪） | 本轮补齐 `by_track` 成功率对照（此前仅逐次日志）；弱模型 / E2E 桩仍依赖 prompt-JSON 回落；双轨共用同一下游（`drafts` + `execute_action` 收口），维护面未翻倍 | `by_track` 中 prompt 轨占比低到阈值（建议 <5% 且连续两个发布窗口）后评估下线 |

> 说明：**「评估」也是完成态**——上表把每项的结论、依据与重开条件写死，方案 §四 不再保留「未评估」悬挂项。

## 5. 边界与遗留（登记，不实施）

- **预签名上传直传**：见 §3.4（安全与一致性成本 > 收益）；
- **计数跳转的两个例外**：字典 `children_count`（子项就在同页树内，跳转无目标）与审批流程 `node_count`
  （节点在设计器内，无独立列表页）保持只读展示；角色 / 数据集 / 部门 / 公告四处跳转已于 ADR-053/054 就位；
- **F-11 邀请激活页密码明文接收**：生产应经 HTTPS（沿用 ADR-053 §4 登记）；
- **P-4 可选依赖**：`django-storages` / `boto3` 默认不装（extras），容器启用时需按 `docs/ops/storage.md` 安装，
  容器重建后需重装（或写进自定义镜像）；
- **AI-1 vision 探测**：默认按钮仍为三项探测（vision 走独立入口），避免无多模态模型上的无谓等待；
- **i18n 长文本**：面向 LLM 的 prompt 模板与 AI 工具描述（约 113 条）不补 zh 译文（非界面文案；
  如需「中文 prompt」再单独评估）；
- **Passkey 登录链路**：`navigator.credentials.get`（登录 MFA 分支）与后端验签同源，仍由后端用例覆盖；
  浏览器侧 E2E 只覆盖注册链路（真实认证器依赖系统级凭据，本地不可编程）。

## 6. 验证

- **后端**：`pytest` 全量 **exit 0** + `ruff check` / `ruff format --check` / 行数门禁（614 文件 0 超标）/
  跨 app / 缓存键 / `makemigrations --check`（No changes detected）/ 文档五件套全绿；
  新增测试：创建即邀请 5、双轨对照 2、统一进度 16、mirror 与预签名 7、依赖 extras 1（共 +31 例）；
- **前端**：`typecheck`（tsc + vue-tsc）/ `typecheck:strict` 全仓 / `eslint --max-warnings 0` / `prettier` /
  `stylelint` / `vitest 340` / 契约 / i18n 词条门禁全绿；改动面：用户表单（邀请开关 + 动态校验）、
  AI 配置页（vision 探测 + 双轨对照）、任务中心（stage 列）、文件中心（高级筛选）、i18n zh/en 各 +9 条；
- **E2E**：新增 `e2e/passkey.e2e.ts`（CDP 虚拟认证器，chromium 1 passed / webkit 按设计 skip）；
  回归 `system-pages` / `ai` / `file-center` / `task-center` / `batch4` / `smoke` **双浏览器 62 passed**；
- **语言包**：zh po 补 150 条用户可见文案 + 修复 brace-format fatal（`msgfmt --check-format` 通过、
  `compilemessages` 全绿）；
- **迁移**：`system/0012_task_progress_stage`（stage 字段 ×2）、`system/0013_ai_usage_track`（track 字段）；
- **部署**（正式环境）：`migrate` → `load_init_json`（本轮无新增权限点 / 配置键，重灌非必需）→ 重启
  daphne / celery-worker / celery-heavy。

## 7. 本地容器全功能验收（2026-09-23）与查漏补缺

在本地 compose 环境（9 容器；为覆盖全部功能域把模块 preset 临时切到 `full`，验毕恢复 `standard`）做了一轮
「每个功能都过一遍」的验收：

- **路由面**：OpenAPI schema 646 条路径、其中 515 条静态可探 → 216 正常 / 129 模块裁剪（预设禁用，预期）/
  165 方法不匹配或二次验证（预期）/ 5 待核 → 全部定位完毕（其中 3 个为真缺陷，见下）；
- **链路面**：按功能域逐项验收 **89 项全通过**（认证 · 用户（F-11 邀请 fail-closed 与「普通创建仍要密码」双断言）·
  组织权限 · 字典 · 文件与访问审计 · 导出与 `stage` · 任务中心 · 标签 · PAT · 我的视图 · Passkey ·
  登录策略 · 账号巡检 · 凭据治理 · 通知与消息模板 · 审批 · 数据分析 · 聊天室 · Webhook · 动态表单 ·
  AI 降级 · 监控 · 日志 · 模块 · 健康）；
- **页面面**：浏览器遍历后端路由表 **54 页**，采集 console error / pageerror / 4xx-5xx API / 渲染内容 →
  **53 页正常渲染**（`tasks/flower`、`swagger/docs` 为 iframe 页无文本，属预期），无失败请求；余下 9 条
  为 DEV 态「父级菜单无 component」噪音（不影响使用）；
- **存储面**：`FILE_STORAGE_BACKEND` 运行期切 `mirror` → 写 / 读 / 删正常，缺对象存储依赖时按设计回退本地
  （告警去重一次），验毕恢复原值。

### 验收中发现并修复的缺陷（均补守护用例）

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | `GET/POST /api/system/auth/verify` 缺 `category` 返回 **HTTP 500**「服务器内部错误」 | 视图内 `getattr(self, f"get_{category}_config")` 直接拼串，category 为 None / 非法即 `AttributeError` | 新增 `CATEGORY_KEYS` 白名单 + `_get_category_config()`，未知 / 缺失返回 1004 可读拒绝；同步更新 2 个固化旧 500 行为的用例并补 `POST` 用例 |
| 2 | `GET /api/settings/ip/block/search-fields` 返回 code 500「获取搜索字段失败」 | `SecurityBlockIpViewSet` 是非模型视图集（内存 queryset、无 `filterset_class`），元数据 Action 直接取 `self.filterset_class.get_filters()` 抛 `AttributeError` | `search_fields` 先判 `filterset_class is None` → 返回空元数据（「没有可筛选字段」是正常语义）；真正的构建异常仍走原失败码 |
| 3 | 标签过滤 `?tag=<主键>` 或 `?tag=A,B`（多标签 AND）返回 **400「不在可用的选项中」** | ① `TagChoiceFilter` 的 `ChoiceField` 只认下拉里的标签名（新建标签的 60s 缓存窗口内、深链带主键均被拦）；② `TagFilterBackend` 对 `getlist("tag")` 的元素未再按逗号切分，多标签被当成单个 token（与 filterset 的 `filter_by_tag_name` 口径不一致，交集恒空） | ① 校验放宽为交过滤器的解析口径（未知标签按空集 fail-closed）；② 逐元素切分逗号，`?tag=A&tag=B` 与 `?tag=A,B` 同义；新增守护用例（主键过滤 / 多标签 AND / 未知标签零结果） |

### 环境提示（排障用，非产品缺陷）

- **系统设置（Setting 表）优先于 `config.yml`**：`SECURITY_LOGIN_CAPTCHA_ENABLED` 等键在库里有行时改文件不生效，
  需改配置项本身（本次验收为自动化登录临时关闭验证码，验毕已恢复 `true`）；
- 容器 `start web` 实际是 **gunicorn（4 worker + `--reload`）**：Python 代码变更自动重载，`config.yml` 变更不重载
  （需重启进程），Setting 变更经信号即时生效；
- 日志在容器内 `/data/xadmin-server/data/logs/`（该目录未挂载，容器重建即失），排障取
  `server.log` / `unexpected_exception.log`。
