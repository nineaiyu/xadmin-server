# ADR-060：聊天附件消息（图片 / 文件）

- 日期：2026-09-27
- 状态：**已交付**（server pytest 全量 EXIT=0 + 16 例附件集成测试 / client typecheck + vitest 611 + eslint + 构建与体积门禁 / E2E 双浏览器 10 例通过 / 本地 compose 已迁移与部署）
- 背景：聊天室仅 text / ai / system 三类消息（对标 ruoyi / jeecg 缺图片与文件消息）；文件中心已有完整的上传安全策略（扩展名白/黑名单、大小、配额、md5 去重、自动分类）与受鉴权取件链路，聊天附件应复用而非另起一套。

## 决策

### D1 消息模型：只存引用 + 元信息快照

- `ChatMessage.MessageType` 新增 `image` / `file`；
- 新增 `attachment` 外键（→ `system.UploadFile`，`SET_NULL`）承载「附件是否仍被业务引用」的判定
  （`UploadFile.has_business_reference` 由此保护磁盘文件不被保留期清理误删）；
- `extra["file"]` 存渲染元信息快照（pk / 文件名 / 大小 / MIME / 分类 / 种类），**取件 URL 由消息主键派生**
  （不在库里存路径——迁移域名或调整路由前缀后历史消息不会失效）；
- 附件随消息落库时把上传件由临时态转正（`is_tmp=False`）：未发送的临时件由每日清理回收，
  已发送的附件不再被临时清理吞掉。

### D2 上传：复用文件中心落库内核（单一安全策略来源）

- 端点 `POST /api/chat/message/upload`（权限点 `upload:ChatMessage`），`kind=image` 时校验确为图片
  （不匹配立即删除刚落的记录并返回 1001，不留无主上传件）；
- 校验/去重/分类/存储抽到 `system/utils/upload_store.py`（`check_upload_limits` + `store_upload_file`），
  文件中心的批量上传与聊天附件共用同一内核——两处各自实现必然漂移；
- 落库为临时件，成功后失效文件统计短缓存并写文件访问审计（`action=upload`）。

### D3 发送：WS 同一条 `chat_message` 帧 + 归属 fail-closed

- 上行帧扩展 `{message_type, file_pk}`（先经上传端点取得 pk，再走 WS）；
- 服务端校验附件归属（**只能引用本人上传件**，他人 pk 一律按「附件不存在」拒绝）；
- 图片消息只接受图片附件（种类匹配在服务端判定，前端不做唯一把关）。

### D4 取件：受鉴权端点 + 房间成员口径

- 端点 `GET /api/chat/message/{pk}/file`（权限点 `file:ChatMessage`）；
- 鉴权 = 登录态 + 消息所在房间可访问（与 `chat_service.accessible_room` 同源），撤回后的消息一律不可取；
- 图片 `?size=thumb|preview` 走预览缓存（JPEG inline，`el-image` 缩略图 + 点击大图），
  其余类型按附件下载（`Content-Disposition: attachment`）；
- **不做逐次访问审计**：图片气泡每次渲染都会取缩略图，逐条留痕会淹没审计日志；
  鉴权口径本身已限定到房间成员（敏感场景的下载审计由文件中心统一承担）。

### D5 前端交互

- 输入区新增「发送图片 / 发送文件」入口（隐藏 `file` input，无第三方上传组件）；
- 气泡：图片走 `el-image`（受鉴权取件 + 点击预览），文件走卡片（文件名 + 大小 + 受鉴权下载）；
  撤回或附件被清理渲染「附件已失效」占位（`extra.file.missing`）；
- 乐观上屏沿用 `client_msg_id` 幂等：上传成功即上屏（进度态），服务端广播回来按 id 覆盖；
  重发沿用同一 `file_pk`（服务端幂等，不重复落库）。

## 验证

- 后端 16 例集成测试：上传（种类门槛 / 黑名单扩展名 / 权限点 fail-closed）、发送（归属、种类匹配、内容缺省取文件名、临时件转正、广播载荷）、取件（成员可读、非成员拒绝、撤回后拒绝、文件下载）、附件失效标记、会话摘要更新；
- E2E（双浏览器）：文件上传 → 卡片渲染 → 图片上传 → 缩略图 → 刷新后历史仍在；
- 权限点守护：`test_chat_menu_seed.py` 表内新增 `upload:ChatMessage` / `file:ChatMessage`，并断言 4 个内置角色均已授权。

## 边界

- 不做语音消息、不做缩略图以外的转码（沿用预览链路）；
- 不做跨用户秒传（md5 去重仍限定同属主，避免越权复用他人存储路径）；
- 聊天附件与文件中心共享 `UploadFile` 记录：文件中心删除该记录会使消息附件失效（前端占位提示，不报错）。
