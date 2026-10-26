# 2026-10 全栈功能审查 · 分册：系统域 B（配置/通知/文件/监控/模块/代码生成 10 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。

## 1. 个人配置（/system/config/user/index）

- **标识**：system · core_config · 薄壳 28 行 · 元数据驱动 · 后端 `system/views/admin/config.py UserPersonalConfigViewSet`。
- **链路**：`userConfigApi` → `UserPersonalConfigViewSet` → `UserPersonalConfig`。
- **后端实现**：继承 SystemConfigViewSet；`registered-keys` 动作显式拒绝（语义错误端点不空转，`config.py:87-93`）；导入导出用专用 Serializer。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 2. 系统配置（/system/config/system/index）

- **标识**：system · core_config · 薄壳 21 行 · 元数据驱动 · 后端 `system/views/admin/config.py SystemConfigViewSet`。
- **后端实现**：destroy 先清缓存再删（`config.py:50-56`）；`registered-keys` 为 `shared_list_action`（运行时按父级 list 权限解析——主册 §3.1 口径补正：该 2 条路由由装饰器注册表覆盖，非缺口，A）；InvalidConfigCacheAction 失效缓存。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 3. 公告管理（/system/notice/index）

- **标识**：notifications · core_notify · 薄壳 33 行 · 元数据驱动 · 后端 `notifications/views/message.py NoticeMessageViewSet`（MessageContent）。
- **后端实现**：publish 动作 PATCH detail（update_fields 白名单只动 publish/modifier，`message.py:50-58`）；announcement 定向创建动作；RecycleBin。
- **闭环校验**：权限 ✅（publish 有独立权限点，全局对账通过）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 4. 公告已读（/system/notice/read/list）

- **标识**：notifications · core_notify · 薄壳 29 行 · 元数据驱动 · 后端 `notifications/views/message.py NoticeUserReadMessageViewSet`（ListDeleteModelSet）。
- **后端实现**：`all-read` 批量动作（PATCH detail=False）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 5. 我的消息（/user/notice/index）

- **标识**：notifications · core_notify · 薄壳 34 行 · 元数据驱动 · 后端 `notifications/views/user_site_msg.py UserSiteMessageViewSet`。
- **后端实现**：**写操作强制过可见域 queryset**——「标记已读、取消已读等写操作必须先过 get_visible_notice_queryset：只按 pk 操作会让任意登录用户改他人消息」（`user_site_msg.py:96-104` 模块注释，防水平越权，A）；未读汇总 10s 短缓存 + 写后失效（:31-66，异常降级不缓存）；unread 过滤器走 Q 对象。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（本人范围 + 防越权写）。
- **问题清单**：无。
- **质量速记**：缓存失效链完整（invalidate_unread_summary），角标轮询热点有专门优化。

## 6. 文件管理（/system/file/index）

- **标识**：file · core_file · `file/index.vue`（75 行：统计条 + 抽屉详情 + RePlusPage）· 元数据驱动 · 后端 `file/views/admin/file.py UploadFileViewSet`（含分片上传 mixin、访问日志 mixin）。
- **后端实现**（本轮最深查页面——文件是攻击面）：
  - 上传：**先全量校验再单事务落库**（任一不合规整体拒绝不落盘，`file.py:243-262`）；扩展名/大小/配额校验与 md5 去重收口在 `file/utils/upload_store.py`（聊天室附件等复用同套策略，模块注释 :59-62）；UploadThrottle 限流（:232-238）；上传后主动失效 stats 缓存 + 逐文件访问审计（:264-268）。
  - 预览：走 DRF 鉴权**不暴露 /media/ 直链**（:159-214）；类型分派后端单一判定（图片/PDF/文本截断/Office 转 PDF heavy 队列，425 Too Early + 1005/1006 业务码）；预览留痕。
  - 受控 lookup 透传（`controlled_lookup = True`，字段面 fail-closed，:91-93）。
- **前端实现**：统计条常驻不挤列表、重型图表走抽屉快照（`index.vue:29-47`）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅（业务码 1002/1003/1004/1005/1006/425 语义清晰）；数据 ✅（配额 + 审计）。
- **问题清单**：无。
- **质量速记**：安全 ✅（上传三重校验 + 事务原子性 + 预览鉴权）；C：Office 转换依赖 LibreOffice 可用性（运行时项）。

## 7. 导出中心（/system/export/index）

- **标识**：task · core_file · `export/index.vue`（41 行，双 Tab：导出/导入，导入 Tab 按权限显隐 + URL 查询同步）· 元数据驱动 · 后端 `task/views/admin/export.py ExportRecordViewSet`。
- **后端实现**：**取值域不走通用数据权限**（默认拒绝会让用户看不到自己的导出）——超管全量、普通用户仅本人（模块注释 :3-11 + RecordOwnerFilter :53-54）；下载经 DRF 鉴权不直出 /media/（导出文件常含敏感数据）；任务日志增量读取（offset/finished）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（本人取值域 + 鉴权下载）。
- **问题清单**：无。

## 8. 主机监控（/system/monitor/index）

- **标识**：system · ops(标配) · `monitor/index.vue`（362 行面板页）· 非标准列表（聚合面板）· 后端 `system/views/platform/monitor.py MonitorViewSet`（overview/services/redis-info/celery）。
- **后端实现**：四个聚合端点统一 10s 缓存（`dashboard_cache_timeout`，`monitor.py:42-93`）；阈值更新 `updateThresholds:SystemMonitor` 前端显式权限门控（`index.vue:41-42`）。
- **前端实现**：加载态/空态（ReEmpty :333）/错误态齐备；红旗扫描 0（无硬编码色/裸表格/!important）。
- **闭环校验**：权限 ✅；元数据 ➖（聚合面板，无列表协议）；契约 ✅；数据 ➖（只读监控）。
- **问题清单**：无。

## 9. 模块管理（/system/module/index）

- **标识**：system · core · `module/index.vue`（337 行）· 非标准列表（模块开关面板）· 后端 `system/views/platform/modules.py`。
- **后端实现**：预设应用走 ChoiceField 白名单（PRESETS）+ enable/disable 清单（`ModuleApplySerializer :91-96`）；**override 写锁**（ReentrantLock，:82-）防并发互踩；payload 含部署基线（deployment_config）。
- **前端实现**：loadError 显式卡片（:87）+ v-loading；红旗扫描 0。
- **闭环校验**：权限 ✅；元数据 ➖；契约 ✅；数据 ✅（写锁 + 预设白名单）。
- **问题清单**：无。
- **质量速记**：C：模块开关对 WS 路由/权限点的即时生效语义需运行时验证（裁剪后旧 token 的菜单缓存）。

## 10. 代码生成（/system/codegen/index）

- **标识**：system（菜单）· devtools 后端（条件注册）· `codegen/index.vue`（499 行向导页）· 非标准列表 · 后端 `system/views/admin/codegen.py SystemCodeGenViewSet`。
- **后端实现**：**只读引擎适配——不落盘不写库**（模块注释 :2-7）；preview 全量渲染返回、download 打包 zip；AI 字段草案人审后进既有管线、异常分级 1001/1002 + 兜底不留内部细节（:176-198）；引擎复用 `generate_crud`（与 CLI 同源，教程镜像门禁覆盖）。
- **前端实现**：动作按钮按权限点 + 状态双重门控（`canPreview = model && hasAuth(...)`，`index.vue:80-115`）；红旗扫描 0。
- **闭环校验**：权限 ✅（models/modelFields/preview/aiDraft/download 各有权限点，全局对账通过）；元数据 ➖；契约 ✅；数据 ✅（纯只读生成）。
- **问题清单**：无。

## 批次 2 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0。
- **正向**：文件域安全设计完整（事务原子上传、预览不直出、访问审计）；导出取值域与数据权限默认值的取舍有显式文档；用户消息防水平越权写。
- **主册口径补正**：`/api/system/config/{system,user}/registered-keys` 2 条路由经 `shared_list_action` 装饰器注册（`system/views/admin/config.py:58,87`），运行时按父级 list 权限解析——主册 §3.1 的「48 条无权限点路由」中这 2 条为离线扫描盲区（装饰器副作用），非缺口。
- **已读文件**（前端 6 + 后端 9）：`file/index.vue`、`export/index.vue`、`config/system/index.vue`、`monitor/module/codegen/index.vue`（结构 + 红旗 grep）、`config/user`、`notice`、`notice/read/list`、`user/notice` 薄壳（wc 确认 <35 行）；`file/views/admin/file.py`、`task/views/admin/export.py`、`system/views/admin/config.py`、`notifications/views/{message,user_site_msg,notifications(结构)}.py`、`system/views/platform/{monitor,modules}.py`（结构段）、`system/views/admin/codegen.py`。
