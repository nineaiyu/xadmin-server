# 公告正文 Markdown 支持开发方案（2026.10）

> 状态：已交付（2026-10-18）　|　影响面：`xadmin-server`（notifications / AI 动作）+ `xadmin-client`（通知公告、消息中心、通知推送）
> 目标：公告支持「富文本 / Markdown」双格式创建与展示，AI 发布的公告默认按 Markdown 渲染
> 预估：核心链路 3.5~5 人日；含 Markdown 编辑器内附件上传 5~7 人日
>
> 落地结果：第一阶段、第二阶段、第三阶段均已实现。后端新增 `message_format` 字段
> （迁移并入 `notifications/migrations/0001_initial.py`）、格式感知校验、推送载荷与
> AI 固定 Markdown；同步更新字段权限种子（`message_format` + 历史遗漏的
> `notice_post` / `deleted_at`）并加种子完整性守护；修复字段树 ROLE/DATA 双根在
> 种子冲突预检中被整棵级联剔除的问题（唯一键改为 `field_type + name + parent`，
> 否则新增字段灌不进已有库）。二开侧新增一条命令
> `python manage.py sync_field_permissions --update-seed`：字段树、本次新增字段
> 的角色白名单与种子一次同步（不动历史裁剪）。前端新增共享
> `MarkdownView`、`NoticeMessageEditor`、`MarkdownEditor`（编辑/分栏/预览三态，
> 分栏复用 `ReSplitPane` 可拖拽调整并持久化宽度），详情/我的通知/铃铛/桌面
> 与应用内预览全部按格式分流；第三阶段完成编辑器内图片/附件上传（按钮/拖拽/粘贴）
> 与 `convert_notice_markdown` 存量转换命令（默认 dry-run）。邮件/IM 渠道转换未启用
> ——当前公告只走站内信，未来接入时按 §10 第 5 条在渠道层分流。
> 上线动作：`python manage.py migrate`；新增 zh 文案随发布镜像执行
> `compilemessages`（本地开发容器同样需要，否则中文界面回退英文）。

## 1. 背景

公告（`MessageContent`）正文当前是富文本 HTML：

- 管理端用 WangEditor 编辑，提交 HTML；
- 服务端 `NoticeMessageSerializer.validate_message` 统一走 `sanitize_rich_text`（bleach 白名单）后入库；
- 展示端 `NoticeShow.vue` 对普通公告用只读 WangEditor，对系统通知用 `v-html` + DOMPurify；
- 顶栏铃铛、桌面通知与 `ElNotification` 用 `stripHtml(message)` 生成预览；
- AI 的 `notice.publish` 动作复用公告管理页的 `announcement` 端点，模型输出的 Markdown 会被当作 HTML 文本入库，展示时原样出现 `**`、`#`、表格与代码块。

与此同时，AI 助手正文已经接入 Markdown 渲染（`src/components/AiMarkdown`），具备可复用的 `markdown-it + DOMPurify + highlight.js` 链路。公告继续只支持富文本，会造成同一系统内「AI 输出 Markdown、公告展示 Markdown 源码」的割裂。

## 2. 目标与非目标

### 2.1 目标

1. 公告创建/编辑支持显式选择「富文本」或「Markdown」，两种格式并存。
2. 公告详情、我的通知、铃铛与桌面通知预览按格式正确展示。
3. AI `notice.publish` 默认以 Markdown 格式发布并正确渲染。
4. 存量富文本公告零变化，无需数据迁移。
5. Markdown 渲染继续遵循现有安全口径：`html: false` + DOMPurify 白名单。

### 2.2 非目标

- 不替换现有 WangEditor 富文本能力。
- 不根据正文内容自动猜测格式（HTML 与 Markdown 存在歧义，自动识别不可靠）。
- 不把存量 HTML 公告批量转换为 Markdown（有损，且会破坏附件/视频等富文本结构）。
- Markdown 编辑器的图片/附件上传按第三阶段落地（一期仅支持 Markdown 链接语法）。
- 不改造邮件/短信/IM 渠道；当前公告发布只走站内信推送。

## 3. 现状代码索引

| 层 | 文件 | 现状职责 |
|----|------|----------|
| 模型 | `notifications/models/message.py` | `MessageContent.message` 为 `TextField`，无格式字段 |
| 序列化 | `notifications/serializers/message.py` | `NoticeMessageSerializer.validate_message` 统一按 HTML 净化；`UserNoticeSerializer`、`NoticeUserReadMessageSerializer` 下发 `message` |
| 视图 | `notifications/views/message.py` | `NoticeMessageViewSet` + `announcement` 动作复用创建链路 |
| 推送 | `notifications/message.py` | `SiteMessageUtil.push_notice_messages` 显式序列化 `pk/level/title/notice_type/message` |
| AI 动作 | `notifications/ai_declarations.py` | `notice.publish` 复用 announcement 端点，`message` 参数为纯文本说明 |
| 前端表单 | `src/views/system/notice/utils/noticeColumns.tsx` | `message` 列固定渲染 `WangEditor` |
| 前端展示 | `src/views/system/components/NoticeShow.vue` | 系统通知 `v-html`；普通通知只读 WangEditor |
| 前端推送 | `src/store/modules/notice.ts` | 桌面通知/`ElNotification` 预览按 HTML 处理 |
| 前端渲染器 | `src/components/AiMarkdown/*` | 已有 Markdown 解析、DOMPurify 净化、highlight.js 按需高亮 |

## 4. 方案选型

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| A. 新增 `message_format` 双格式 | 显式标记 `html` / `markdown`，按格式编辑与展示 | 存量零变化；Markdown 源码可继续编辑；安全边界清晰 | 增加字段、迁移与展示分支 | **推荐** |
| B. Markdown 转 HTML 后只存 HTML | 保存时把 Markdown 渲染成 HTML | 展示链路不变 | 源码丢失、二次编辑回显 HTML；需要服务端 Markdown 依赖 | 不采用 |
| C. 自动识别格式 | 无字段，按内容特征猜测 | 无模型改动 | HTML 中可能含 Markdown 字符；纯文本与 Markdown 无法可靠区分 | 不采用 |
| D. 全量替换为 Markdown | 下线富文本，统一 Markdown | 存储与渲染单一 | 破坏存量公告、附件/视频/图片能力 | 不采用 |

推荐方案 A：**保留富文本，新增 Markdown 作为并列格式**，格式在创建时确定、编辑时不可切换。

## 5. 总体设计

### 5.1 数据模型

`MessageContent` 新增：

```python
class MessageFormat(models.TextChoices):
    HTML = "html", _("Rich text")
    MARKDOWN = "markdown", _("Markdown")


message_format = models.CharField(
    max_length=16,
    choices=MessageFormat.choices,
    default=MessageFormat.HTML,
)
```

- 存量行由迁移默认值自动落为 `html`，不需要数据回填。
- 格式创建后不可修改：避免 HTML 与 Markdown 互相转换造成内容丢失。
- 系统通知（后端模板生成的 HTML）继续使用默认值 `html`，行为不变。

### 5.2 格式语义

| 格式 | 存储 | 服务端写入校验 | 展示 |
|------|------|----------------|------|
| `html` | 富文本 HTML | `sanitize_rich_text`（bleach 白名单） | 只读 WangEditor / `v-html` + DOMPurify（现状） |
| `markdown` | Markdown 源码 | 保留源码，仅做长度与空值校验；**不跑 bleach** | `markdown-it(html: false)` → DOMPurify → `v-html` |

Markdown 源码中的原始 HTML 不会被执行：`html: false` 会转义，展示层再经 DOMPurify 兜底。任何新增消费方都必须尊重 `message_format`，禁止把 Markdown 源码直接交给 `v-html`。

### 5.3 后端链路

1. `NoticeMessageSerializer.Meta.fields` 增加 `message_format`。
2. `validate_message` 按格式分流：
   - `html`：继续 `sanitize_rich_text(value)`；
   - `markdown`：保留源码，校验最大长度（建议 100,000 字符）。
3. `validate()` 禁止更新时修改 `message_format`。
4. `UserNoticeSerializer.fields`、`NoticeUserReadMessageSerializer.notice_info` 增加 `message_format`，供「我的通知」与铃铛列表使用。
5. `SiteMessageUtil.push_notice_messages` 的显式字段列表增加 `message_format`，供 WebSocket 推送预览分流。
6. AI `notice.publish` 的 `defaults` 增加 `message_format: "markdown"`（服务端固定值，不进入 LLM 参数目录），参数说明改为「Markdown 正文」。

### 5.4 前端链路

1. 抽出共享 Markdown 渲染器 `MarkdownView`（可由现有 `AiMarkdown` 的核心逻辑下沉）：
   - 解析与净化集中在 `src/utils/markdown.ts` 或 `src/components/MarkdownView`；
   - 代码高亮复用 highlight.js 按需加载；
   - 提供 `variant="document"` 的文档型排版，不套 AI 气泡样式。
2. `NoticeShow.vue` 按 `message_format` 分流：
   - `markdown`：`MarkdownView`；
   - 系统通知：现有 `v-html` + `sanitizeHtml`；
   - 其他：现有只读 WangEditor。
3. 管理端表单增加「正文格式」选择：
   - 新增时可选「富文本 / Markdown」；
   - 编辑时锁定格式；
   - `message` 列改为 `NoticeMessageEditor` 包装组件，按格式渲染 WangEditor 或 Markdown 编辑器。
4. Markdown 编辑器的第一版采用「textarea + 实时预览」：
   - 左侧等宽字体输入，右侧预览；
   - 窄屏切换「编辑 / 预览」；
   - 不引入新的重型编辑器依赖；预览复用 `MarkdownView`。
5. 通知预览：
   - `src/store/modules/notice.ts` 按 `message_format` 分流；
   - Markdown 使用 `markdownToPlainText`（渲染为 HTML 后剥离标签）生成桌面/`ElNotification` 纯文本预览，避免出现 `**`、`#`；
   - 完整详情弹窗仍渲染 Markdown。
6. 类型与词条：
   - 前端补充 `MessageFormat = "html" | "markdown"` 类型；
   - `zh-CN.yaml` / `en.yaml` 增加「富文本 / Markdown / 编辑 / 预览 / 格式创建后不可修改」等词条；
   - 后端 choices 的 `Rich text` / `Markdown` 补 zh 译文。

## 6. 详细改动清单

### 6.1 后端

| 文件 | 改动 |
|------|------|
| `notifications/models/message.py` | 新增 `MessageFormat` 与 `message_format` 字段 |
| `notifications/migrations/0001_initial.py` | 字段并入初始迁移（默认 `html`），不新增独立迁移 |
| `notifications/seeds/modellabelfield.json` | 角色字段树新增 `message_format` 节点 |
| `notifications/seeds/fieldpermission.json` | 通知公告相关白名单补 `message_format`，并补齐历史遗漏的 `notice_post` / `deleted_at` |
| `notifications/serializers/message.py` | 字段下发；格式感知的 `validate_message`；编辑期格式不可变；用户通知序列化器补字段 |
| `notifications/message.py` | 推送载荷补 `message_format` |
| `notifications/ai_declarations.py` | `notice.publish` 的 const 默认值与参数说明改为 Markdown |
| `tests/unit/notifications/test_notice_input_validation.py` | HTML 净化、Markdown 保留、非法格式、格式不可变 |
| `tests/integration/ai/test_ai_action.py` | AI 公告落库 `message_format="markdown"` |

`validate_message` 示意：

```python
def _requested_format(self):
    value = self.initial_data.get("message_format")
    if isinstance(value, dict):
        value = value.get("value")
    if value:
        return value
    if self.instance is not None:
        return self.instance.message_format
    return MessageContent.MessageFormat.HTML


def validate_message(self, value):
    if self._requested_format() == MessageContent.MessageFormat.MARKDOWN:
        if len(value or "") > MAX_MARKDOWN_LENGTH:
            raise ValidationError(_("The Markdown content is too long"))
        return value
    return sanitize_rich_text(value)
```

### 6.2 前端

| 文件 | 改动 |
|------|------|
| `src/components/MarkdownView/*`（或复用 `AiMarkdown` 的 core） | 文档型 Markdown 渲染；统一净化与高亮 |
| `src/utils/markdown.ts` | `renderMarkdown` / `markdownToPlainText` 收口 |
| `src/views/system/components/NoticeShow.vue` | 按 `message_format` 分流；Markdown 分支不加载 WangEditor |
| `src/views/system/notice/utils/noticeColumns.tsx` | 新增 `message_format` 列处理器；`message` 列改为 `NoticeMessageEditor`，占满整行 |
| `src/views/system/notice/components/NoticeMessageEditor.vue` | 按格式切换 WangEditor / MarkdownEditor |
| `src/views/system/notice/components/MarkdownEditor.vue` | 编辑/分栏/预览三态；分栏复用 `ReSplitPane` 可拖拽调整；实时预览 |
| `src/views/system/notice/components/markdown/MarkdownToolbar.vue` | 格式工具栏（标题/粗斜体/删除线/引用/代码/链接/表格/分割线/上传/视图切换） |
| `src/views/system/notice/components/markdown/useMarkdownUpload.ts` | 图片/附件上传：按钮、拖拽、粘贴、多文件、大小校验与语法插入 |
| `src/store/modules/notice.ts` | 推送预览按格式分流；Markdown 转纯文本 |
| `src/api/system/notice.ts` 或同域类型文件 | 补充 `MessageFormat` 类型与表单载荷 |
| `locales/zh-CN.yaml` / `locales/en.yaml` | 新增格式、编辑、预览相关词条 |

`NoticeShow.vue` 分流示意：

```vue
<MarkdownView
  v-if="isMarkdown"
  :content="newFormInline.message ?? ''"
/>
<div
  v-else-if="newFormInline.notice_type?.value === NoticeChoices.SYSTEM"
  v-html="sanitizedMessage"
/>
<div v-else class="wangeditor">
  <Editor v-model="newFormInline.message" read-only />
</div>
```

`NoticeMessageEditor` 分流示意：

```vue
<WangEditor v-if="format === 'html'" v-model="modelValue" @change="onHtmlChange" />
<MarkdownEditor v-else v-model="modelValue" />
```

### 6.3 API 载荷示例

创建 Markdown 公告：

```json
{
  "title": "系统维护公告",
  "notice_type": 1,
  "publish": true,
  "level": "info",
  "message_format": "markdown",
  "message": "## 维护窗口\n\n- 时间：今晚 23:00\n- 预计 30 分钟\n\n```bash\nsystemctl restart xadmin\n```"
}
```

响应中的 `message_format` 由 `LabeledChoiceField` 下发为 `{ "value": "markdown", "label": "Markdown" }`；前端按现有 `notice_type` / `level` 的取值方式统一读取 `.value`。

## 7. 实施阶段与验收

### 第一阶段：后端 + 只读展示（约 1.5~2 人日）

- 模型字段与迁移；
- 序列化器分流、字段下发、推送载荷；
- AI `notice.publish` 默认 Markdown；
- `NoticeShow` Markdown 分支与共享渲染器；
- 铃铛/桌面通知纯文本预览；
- 后端与前端展示测试。

验收：AI 发布一条 Markdown 公告后，站内信详情正确渲染标题、列表、表格、代码块；存量富文本公告展示不变。

### 第二阶段：编辑入口（约 2~3 人日）

- 表单「正文格式」选择与编辑态锁定；
- `NoticeMessageEditor` + `MarkdownEditor`；
- 切换格式时非空正文二次确认并清空；
- E2E 增加 Markdown 公告创建与查看。

验收：管理端可创建 Markdown 公告；保存后刷新仍以 Markdown 编辑/展示；富文本公告流程零回归。

### 第三阶段（已完成）

- Markdown 编辑器图片/附件上传，并回填 `files` 供 `AutoCleanFileMixin` 管理（已实现；
  编辑器支持按钮、拖拽、粘贴上传，多文件顺序插入，上传按钮只依赖上传权限；
  E2E 覆盖「选择附件 → 上传 → 插入语法 → 保存 → 详情链接」）；
- 存量 HTML 公告转 Markdown（有损，默认 dry-run，`--apply` 才写库；
  命令：`python manage.py convert_notice_markdown`）；
- 若未来公告走邮件/IM，在渠道层增加 Markdown → HTML/纯文本转换（本期未启用：
  公告只走站内信，非目标；接入时按 §10 第 5 条分流）。

## 8. 测试计划

### 8.1 后端

- HTML 内容仍被 `sanitize_rich_text` 净化（脚本/事件属性被剥离）；
- Markdown 内容原样保存（`#`、`**`、表格、围栏代码块不被转义）；
- 非法 `message_format` 返回 400；
- 编辑时修改 `message_format` 返回可读校验错误；
- announcement 端点按 `message_format` 落库；
- 推送载荷包含 `message_format`；
- AI `notice.publish` 创建 `markdown` 公告且权限/审计链路不变。

### 8.2 前端

- `NoticeShow` Markdown 分支渲染标题/列表/表格/代码；
- HTML 分支仍走只读 WangEditor；
- 表单格式切换与编辑态锁定；
- Markdown 编辑器输入/预览/保存回写；
- Markdown 编辑器附件上传：接口调用、语法插入、`files` 回填；
- 通知 store 的 Markdown 预览不出现 Markdown 标记；
- 组件与 hook 单测覆盖新增分支。

### 8.3 E2E 与门禁

- `e2e/notice-crud.e2e.ts` 增加 Markdown 公告创建 → 列表 → 详情渲染；
- `e2e/user-notice.e2e.ts` 验证我的通知详情；
- `pnpm test:run`、`pnpm typecheck`、`pnpm lint`、`pnpm build`、`check:bundle-size`；
- 后端 `ruff`、文件/函数行数、迁移漂移、i18n 门禁。

## 9. 兼容性、迁移与回滚

- 存量公告默认 `html`，不需要数据迁移。
- 新增字段为加法变更；旧前端不传该字段时按 `html` 处理。
- 迁移已按要求并入 `0001_initial`：全新库直接建列；**已执行过旧 `0001_initial` 的存量库不会重跑初始迁移**，升级时需补列
  `ALTER TABLE notifications_messagecontent ADD COLUMN message_format varchar(16) NOT NULL DEFAULT 'html'`
  （或重新初始化）。发布前应确认目标环境的迁移账本。
- 回滚：回退代码后字段可保留（旧代码不读取），或回滚迁移删除列；两种方式都不影响存量数据。
- 历史 AI 公告如果已经是 Markdown 文本但落在 `html`，可通过 `OperationLog(module="AI:action")` 定位后批量改为 `markdown`；不作为本期必做项。

## 10. 安全要求

1. Markdown 渲染必须始终使用 `markdown-it({ html: false })` + DOMPurify 白名单；禁止直接 `v-html` Markdown 源码。
2. 外链统一补 `target="_blank"` 与 `rel="noopener noreferrer"`。
3. 服务端对 Markdown 只做长度上限与空值校验，不跑 bleach（避免破坏源码）；安全由统一渲染器保证。
4. 推送预览只使用纯文本，不把 Markdown 渲染结果塞进 `ElNotification` 的 HTML 通道。
5. 新增任何消费方（导出、邮件、IM、开放平台）都必须按 `message_format` 分流。

## 11. 风险与待确认

| 项 | 说明 | 建议 |
|----|------|------|
| 编辑时切换格式 | 保存的只有一份正文，切换会改变解释方式 | 创建后不可切换；如必须支持，单独立项评估 HTML→Markdown 转换 |
| 编辑器形态 | 完整 Markdown 编辑器（Vditor / md-editor-v3）体积与 CSP 面较大 | 一期 textarea + 实时预览；有强需求再评估 |
| 附件/图片上传 | Markdown 模式下没有现成工具栏 | 第三阶段已实现（系统上传接口 + 语法插入 + files 回填） |
| 历史 AI 公告 | 旧数据可能已是 Markdown 但标记为 html | 可选批量回填；新公告不受影响 |
| 搜索 | `message` 过滤检索的是原始源码 | 可接受；如需按渲染文本搜索需额外索引 |
| 双格式维护 | 两条展示路径长期并存 | 共享 `MarkdownView` 与统一净化入口，避免分叉 |

## 12. 验收标准

- [x] 管理端可创建、编辑、查看 Markdown 公告，编辑态仍以 Markdown 源码呈现。
- [x] 存量富文本公告的创建、编辑、详情、推送行为零变化。
- [x] AI `notice.publish` 发布的公告默认按 Markdown 渲染。
- [x] 我的通知、铃铛详情、桌面/应用内通知预览均按格式正确展示。
- [x] Markdown 中的原始 HTML、危险协议链接不能进入 DOM。
- [x] 后端与前端测试、类型检查、lint、构建与体积门禁全部通过。

## 13. 参考

- AI 助手 Markdown 渲染：`xadmin-client/src/components/AiMarkdown/`
- 公告展示：`xadmin-client/src/views/system/components/NoticeShow.vue`
- 公告表单元数据：`xadmin-client/src/views/system/notice/utils/noticeColumns.tsx`
- 公告序列化与净化：`xadmin-server/notifications/serializers/message.py`
- AI 动作声明：`xadmin-server/notifications/ai_declarations.py`
