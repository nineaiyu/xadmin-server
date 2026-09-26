# ADR-033：AI 知识库文档管理（上传 / 预览 / 启停）

- 状态：已接受（2026-09-14 实现落地）
- 关联：[ADR-023](ADR-023-ai-assistant-phase1.md)（AI 助手一期：docs/ RAG 问答）；
  [ADR-024](ADR-024-nl-query-phase2.md)（NL 查数）；ADR-015 重依赖红线（不引 markdown 渲染依赖）；
  `ai/models/ai.py`、`system/utils/ai.py`、`system/views/ai.py`

## 背景

一期（ADR-023）知识库**只来自仓库 `docs/` 目录**（`sync_ai_knowledge` 命令扫描，hash 幂等）。
实际使用中产品/客服等角色需要把**自己的使用手册、内部规范**录进知识库，但：

- 他们不维护代码仓库，无法把 markdown 放进 `docs/` 再跑命令；
- 也没有任何界面能查看"当前知识库里有哪些文档、问答时会被切成哪些块"。

本期的目标：管理端「知识库」页——上传文档、预览内容与分块、启停、删除、重建仓库同步，
且**不破坏一期检索链路**（词频检索、ask/nl-query 全部复用同一分块表）。

## 决策

### 1. 双来源统一登记：`AiKnowledgeDocument` + 既有分块表

```
AiKnowledgeDocument（文档登记：repo | upload）
  ├─ path（唯一；repo=相对路径 docs/xxx.md，upload=upload/{name}.md）
  ├─ title / content（全文）/ content_hash（全文 hash）/ chunk_count / is_active
  └─ 分块仍落 AiKnowledgeChunk（source_path = path，前缀 upload/ 隔离来源）

检索面 = 分块表（retrieve 零改动）
  ├─ 上传/覆盖 → 重建该文档分块 → 立即参与问答
  ├─ 停用     → 移除分块（内容保留，可再启用重建）
  └─ 删除     → 移除分块 + 文档记录
```

- **检索链路零改动**：`retrieve` 仍全表扫分块，文档的可见性由"有没有块"天然表达；
- 分块逻辑与一期同源（`_chunk_markdown`：`##` 边界 + 1200 字滑窗），预览页展示的分块
  与实际检索引擎一致，可核对分块合理性。

### 2. 上传 = 文本入库（不落文件系统）

- 管理页"选择本地文件"由**浏览器 FileReader 读取为文本**填充编辑区，用户可再编辑后提交
  （与直接粘贴走同一 JSON 接口 `{name, content}`）；
- 服务端不接收二进制文件、不落磁盘：md 是文本资产，落库即完成，无文件生命周期与病毒扫描面；
- 校验：名称必填 ≤120 字符且不含路径字符（`/`、`\`、`..`，防破坏 `upload/` 前缀隔离）；
  全文 ≤20 万字符；同名（稳定 path）视为**覆盖更新**——列表不会出现同名多份，
  "重新上传即更新"符合文档维护直觉。

### 3. 预览：详情全文 + 分块摘要，列表轻量

- `retrieve` 返回全文 `content` 与分块摘要 `chunks[{index,size,preview}]`；
- 列表经 `to_representation` 裁剪掉 `content/chunks`（15 条 × 全文的响应体积不可接受）；
- 全文按 **Markdown 原文**展示（`whitespace-pre-wrap`，与助手页回答渲染同口径）：
  不引入 md 渲染依赖（ADR-015 红线），也避免第三方内容渲染的 XSS 面。

### 4. 同步边界：sync 只维护 repo，upload 不越界

`sync_knowledge` 重写为文档表驱动：

- 每个仓库文件 upsert `AiKnowledgeDocument(source_type=repo)`，全文 hash 相同则跳过（快路径），
  块被外部清理时自愈重建；仓库文件消失 → 删文档 + 分块；历史遗留的无登记孤儿块一并清理；
- **upload 来源与 `upload/` 前缀分块完全不参与扫描与清理**（守护测试
  `test_sync_keeps_upload_documents` 钉死）；
- `sync-repo` action：管理端手动触发重扫（部署侧仍可跑命令/加定时）。

### 4. **批量操作 = 框架内建批量删除 + 自定义批量启停**
   - 批量删除：勾选行后 RePlusPage 工具栏自动出现「批量删除」（`auth.batchDestroy` +
     popconfirm 计数确认），调 `POST batch-destroy [pks]`。后端**不复用**框架
     `BatchDestroyAction`（其非逐行分支走 `queryset.delete()`，不触发 perform_destroy，
     分块会残留成孤儿块继续参与检索），改为自带 `@action` 的覆写：只处理 upload、
     逐条 `remove_chunks` 后删除，repo 静默保留；
   - 批量启用/停用：`POST batch-toggle {pks, is_active}`——停用移除分块（退出检索），
     启用重建分块，返回变更条数；与单条 partialUpdate 走同一 `set_document_active`；
   - 权限点扩展为 8 个（新增 `batchDestroy` / `batchToggle`）。

5. 权限与菜单

页面 `AiKnowledge`（集成管理 → 知识库，rank 7）+ 6 个权限点：
`list/create/retrieve/partialUpdate/destroy/syncRepo`（种子 `menu.json` + `menumeta.json`）。
仓库文档的删除入口在服务端拒绝（`Repository documents are managed by sync`），
前端对 repo 行也不渲染删除按钮（双保险）。

## 后果

- 产品/客服可自助维护指南类文档，问答引用出处会带上 `upload/xxx.md` 路径；
- 知识库规模由"仓库文档"扩展为"仓库文档 + 上传文档"，检索耗时随块数线性增长
  （词频评分全表扫，管理面规模可接受；向量检索仍是评估出口）；
- 明示不做：富文本/markdown 渲染预览（原文展示）、草稿与版本历史、文件夹分类、
  多语言文档、上传附件/图片（知识库只保留文本）。

## 实现落地记录（2026-09-14）

- `ai/models/ai.py`：`AiKnowledgeDocument`（迁移 `0006_aiknowledgedocument`），
  `UPLOAD_PATH_PREFIX` / `upload_document_path`；
- `system/utils/ai.py`：`rebuild_chunks` / `remove_chunks` / `upsert_upload_document` /
  `set_document_active`，`sync_knowledge` 重写（文档表驱动 + upload 隔离 + 孤儿块清理）；
- `system/serializers/ai.py`：`KnowledgeUploadSerializer`（名称/内容/大小校验）、
  `AiKnowledgeDocumentSerializer`（列表轻量 / 详情全文+分块 / is_active 联动分块）；
- `system/views/ai.py`：`AiKnowledgeDocumentViewSet`（上传同名覆盖 / 删除仅 upload /
  `sync-repo`）；
- 菜单种子：页面 + 6 权限点（menu.json/menumeta.json）；po 词条 zh/en；
- 前端：`src/api/system/knowledge.ts`、`src/views/integration/knowledge/`（RePlusPage +
  上传弹窗 FileReader + 预览抽屉全文/分块 + 停用启用），登录页词条 zh/en；
- 测试：`tests/integration/system/test_ai_knowledge.py`（上传/覆盖/校验/预览/删除/启停/
  sync 隔离/权限）、`test_ai_assistant.py` 同步语义适配；E2E `knowledge.e2e.ts` 全链路。

## 验收（已执行）

- 集成测试 17 项 + AI 助手既有用例全绿；前端 typecheck / eslint / locale-keys / vitest 绿；
- E2E 管理面全链路（上传 → 列表分块数 → 预览全文 + 分块 → 停用启用 → 删除）；
- 正式库：迁移 + 种子重灌后，仓库 476 块与上传文档并存，检索同时命中两类来源。

## 踩坑记录（2026-09-14 排查，均已修复并加守护）

1. **表格页 ViewSet 必须同时混入 `SearchFieldsAction` + `SearchColumnsAction`**：漏掉
   SearchColumnsAction 时 `search-columns` 路由不存在 → 列元数据缺失 → RePlusPage 列定义为空
   → 表格 `<tr>` 存在但无 `<td>`，表现为「列表有数据却整片空单元格」，极易误判为后端数据问题。
   已补守护 `test_viewsets_with_table_fields_expose_search_columns`（判定：有 list + 序列化器
   声明 table_fields 的视图集必须混入，面板统计类借用序列化器的视图不在判定范围）；
2. **RePlusPage 行内取值为字典化对象**：`source_type` 等 LabeledChoice 字段下发 `{value,label}`，
   行级判断必须取 `.value`——删除按钮的 `source_type === "upload"` 曾恒为 false 而不渲染；
3. **`ButtonOperation` 的 `text`/`show` 回调是位置参数 `(row, button)`**，`onClick` 收的才是
   `{ row, loading }` 对象：`show: ({ row }) => ...` 解构会拿到 undefined 并在行渲染期抛错，
   Vue 更新中断（表格 DOM 停在首行，其余行 tr 无子节点）；
4. **代码挂载的容器要重启进程**：`xadmin-server` 以 `./:/data/xadmin-server` 挂载，
   改完后端代码（视图/序列化器/模型）必须 `docker restart` 才加载新模块；po 变更还需
   容器内 `compilemessages`。新增菜单/权限点还要重灌 `load_init_json`（前端 `hasAuth`
   依赖 routes 下发的权限码清单）。
5. **覆写框架的 `batch_destroy` 必须自带 `@action`，且不要再混入 `BatchDestroyAction`**：
   DRF 按方法的 `mapping` 属性收集额外路由，无装饰器的覆写会让 `batch-destroy` 变成 405；
   同时混入一个自带 `@action` 的同名方法会造成装饰器叠加。
6. **drf-spectacular 的 schema 参数位置**：`OpenApiRequest` 与 `build_array_type` 都
   **不接受 `description`**（只有 `build_object_type` 支持）。写错会在**模块导入期**
   抛 `TypeError` → URLconf 整体加载失败，表现为「一堆看似无关的测试同时挂」
   （本次连带 3 个 AI 配置用例和两个守护测试失败），排查时先看模块导入栈。
