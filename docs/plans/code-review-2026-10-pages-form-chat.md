# 2026-10 全栈功能审查 · 分册：表单采集与聊天室（4 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。

## 1. 表单设计器（/form-collection/designer/index）

- **标识**：dataset · dform(可选) · 设计器画布（自定义，非列表协议）· 后端 `dataset/views/dform.py DynamicFormViewSet`（BaseModelSet + ImpactPreviewAction）。
- **后端实现**：schema 写入侧校验（模块注释 :5）；列表走轻量序列化器（只渲染字段数，schema 全文仅详情/编辑——列表载荷收口，`dform.py:64-66`）；删除表单有逐行副作用（绑定流程的 form_schema 再同步，`dform.py:115-120`）。
- **闭环校验**：权限 ✅；元数据 ➖（设计器）；契约 ✅；数据 ✅（删除副作用显式管理）。
- **问题清单**：无。
- **质量速记**：C：设计器 XSS 面（组件属性注入）属前端专项+运行时复核范围。

## 2. 我的填报（/form-collection/my/index）

- **标识**：dataset · dform(可选) · 元数据驱动（动态列）· 后端 `dataset/views/dform_submission.py DynamicFormSubmissionViewSet`。
- **后端实现**：**本人取值域**（普通用户只见/只改本人提交，超管全量，模块注释 :5）；「Only the creator can modify」写保护（:40）；导出自动跟随列表筛选并保持 creator 隔离（:8）；提交触发审批流（`dform_flow.create_flow_instance`，resubmit 重投）。
- **闭环校验**：权限 ✅（creator 隔离 + 写保护）；元数据 ✅；契约 ✅；数据 ✅（填报↔审批流联动）。
- **问题清单**：无。

## 3. 表单数据（/form-collection/data/index）

- **标识**：dataset · dform(可选) · 元数据驱动（schema 展开动态列）· 后端 `dataset/views/form_data.py DynamicFormDataViewSet`。
- **后端实现**：**取值域走数据权限编译器而非 creator 隔离**（管理视角，模块注释 :7——与「我的填报」两个取值域口径并存且各自显式声明）；MaterializedFilterMixin 物化列筛选。
- **闭环校验**：权限 ✅（数据权限编译器 fail-closed）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：同模型两视图（我的/管理）取值域差异显式文档化——防「复制视图时把隔离语义带丢」的常见二开事故。

## 4. 聊天室（/chat/index）

- **标识**：message · chat(可选) · IM 界面（自定义）· 后端 `message/views.py ChatRoomViewSet / ChatMessageViewSet / ChatContactViewSet` + `views_ai.py ChatAiViewSet`（聊天室内 AI 助手）+ WS `message/ws/`。
- **后端实现**：
  - **房间访问统一门**：历史消息等先过 `chat_service.accessible_room(room, user)`（`views.py:207-211`，非成员不可读）；
  - 附件上传**复用文件中心安全内核**（扩展名/大小/配额/md5/分类）+ `kind` 白名单且必须与真实种类一致（不匹配删记录返回 1001，防伪造 kind 错误渲染，`views.py:237-249`）；UploadThrottle 限流；临时件生命周期（发送转正/每日清理回收）；
  - 历史游标分页：before_id 校验 + limit 双向夹取（:215-224）；
  - 撤回动作（recall）、私聊开房（open-private）、群建/成员/改名/退出均以成员身份收口（GenericViewSet + 服务层校验，B）。
- **闭环校验**：权限 ✅（成员制 + 白名单 URL/权限点对账通过，主册 §3.1）；元数据 ➖（IM）；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：C：WS 通道（`ws/chat/`）的成员校验与离线消息属运行时行为；模块裁剪后 WS 4404 语义有守护测试（test_plugin_demo 提及 WS 4404）。

## 批次 7 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0。
- **正向**：同模型双视图取值域差异显式化；聊天附件复用文件中心内核（安全策略单源）；kind 与真实种类一致性校验。
- **已读文件**：`dataset/views/{dform,dform_submission,form_data}.py`（关键段）、`message/views.py`（69-250）、`message/views_ai.py`（结构）；前端 4 页结构确认（designer 画布 / chat IM 自定义形态）。
