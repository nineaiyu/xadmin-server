# 组件手册 · 前端组件（xadmin-client）

> 本文为《组件手册》前端分册（§二）。全景图、工程化设施与扩展点速查见
> [component-handbook.md](component-handbook.md)；后端组件见 [handbook-backend.md](handbook-backend.md)。

## 二、前端组件（xadmin-client）

### 2.1 RePlusPage 体系（列表页一体化组件）

**组成**（`src/components/RePlusPage/`）：页面组件 + 取数/列装配/表单/按钮四组内部 hook + 内置子组件（AddOrEdit / ImportData / ExportData / ChangeHistoryDialog / ReRecycleBin / ButtonOperation…）。

**基本用法**（页面 = 薄壳 + hook）：

```vue
<!-- views/crm/customer/index.vue -->
<script lang="ts" setup>
import { useCustomer } from "./utils/hook";
defineOptions({ name: "CrmCustomer" });   // 必须唯一：权限码 动作:组件名 的匹配键
const { api, auth } = useCustomer();
</script>
<template>
  <RePlusPage :api="api" :auth="auth" locale-name="crmCustomer" />
</template>
```

| 项 | 清单 |
|---|---|
| 关键 props | `api` / `auth` / `localeName` / `selection` / `operation` / `tableBar` / `immediate` / `isTree` / `recycleBin` / `pagination` / `addOrEditOptions` / `operationButtonsProps` / `tableBarButtonsProps` / 五个 `*Format` 格式化出口 / `beforeSearchSubmit` |
| emits | `rowClick` / `searchComplete` / `selectionChange` / `tableBarClickAction` / `operationClickAction` |
| expose | `dataList` / `searchFields` / `getTableRef` / `getSelectPks` / `getPageColumn` / `handleGetData` / `handleAddOrEdit` |
| 权威源 | `src/components/RePlusPage/src/utils/types.ts`（`RePlusPageProps`） |

**格式化出口**（定制渲染的主入口；按下列执行顺序，后执行的覆盖先执行的）：

| 出口 | 时机 |
|---|---|
| `searchResultFormat(result)` | 列表响应后、渲染前（改行数据） |
| `listColumnsFormat` / `detailColumnsFormat` / `searchColumnsFormat` | 对应列集合装配后 |
| `baseColumnsFormat({listColumns, detailColumns, searchColumns, addOrEditRules, addOrEditColumns, ...})` | 最后统一改写（含表单规则） |

**操作按钮体系**（`ButtonOperation`，权威源 `src/components/RePlusPage/src/components/ButtonOperation/src/types.ts`）：

```ts
:operationButtonsProps="{ showNumber: 5, width: 260, buttons: [{
    text: t('customer.export'), code: 'export',
    show: auth.exportData && 10,            // 数字 = 排序并显示；false = 隐藏
    confirm: { title: t('customer.exportConfirm') },
    onClick: ({ row, loading }) => { /* 回调参数是对象 */ }
}] }"
```

- 默认按钮（编辑 -30 / 删除 -20 / 详情 -10 / 变更历史 -5）与自定义按钮**追加合并**；`show` 传数字决定位置与显隐。
- **坑**：`text` / `show` 的函数签名是 `(row, button)` 位置参数，只有 `onClick` 收对象 `{row, loading}`。

**弹层表单与提交**（`usePlusPageForm` + `handle-dialog.ts`）：

- 默认新增/编辑：`api.create` / `api.partialUpdate`；编辑态先 `detail(pk, {mask: "false"})` 回取脱敏原文。
- 定制：`addOrEditOptions.props.columns/row/formProps`（列解析器 ctx 含 `column / isAdd / formValue`）+ `beforeSubmit`（提交前加工）+ `apiReq`（换接口）。
- 服务端校验失败自动内联到表单项（`src/components/RePlusPage/src/utils/serverErrors.ts`）。

**复用列装配**（非 RePlusPage 页面）：`useBaseColumns(localeName)` 产出六组列/规则，范例 `src/views/settings/components/settings/SettingItem.vue`。

- 依赖：`@pureadmin/table`（PureTable/PureTableBar，必需）、`plus-pro-components`（PlusSearch/PlusForm，必需）、`src/utils/http`、图标方案。
- 扩展点：渲染器注册（§4.2）、`openDialogDrawer`（复用弹层表单能力）、`handleOperation`（请求 + 提示 + 回调的标准封装）。
- 移植说明（去 xadmin 化）：`xadmin-client/docs/metadata-driven-crud.md`。

### 2.2 弹层：ReDialog / ReDrawer

```ts
// 命令式：任意位置调用，不需要在模板挂组件
addDialog({
  title: t("crm.customer.importTitle"),
  width: dialogSize("md"),            // 四档尺寸 sm=480 / md=640 / lg=760 / xl=860
  draggable: true, destroyOnClose: true, closeOnClickModal: false,
  sureBtnLoading: true,               // 异步确认按钮 loading
  contentRenderer: () => h(ImportForm, { onSaved: refresh }),
  beforeSure: async (done, { closeLoading }) => { /* 校验/提交，失败不调 done */ }
});
addDrawer({ title, size: "55%", props: { pk: row.pk }, contentRenderer: () => h(DetailPanel) });
```

| 项 | 说明 |
|---|---|
| 导出 API | `addDialog` / `closeDialog` / `updateDialog` / `closeAllDialog` / `getDialogUid`（ReDrawer 对称） |
| 关键配置 | `title/width/modal/fullscreen/draggable/destroyOnClose/closeOnClickModal`、`props`（内容组件 props）、`hideFooter`、`footerButtons`、`beforeSure` / `beforeCancel`、`open/close` 回调 |
| 内容组件约定 | 通过 `props` 收参；关闭用框架 close 事件（勿声明 `onClose` prop）；提交成功后**先 `done()` 关弹窗再 `await` 刷新列表**（先刷新会滞留） |
| 何时用哪个 | 表单/确认弹窗 → ReDialog；复杂详情/长表单/侧栏编辑 → ReDrawer；一句确认 → `ElMessageBox`（危险操作用 `el-button--danger` + `.catch` 兜底） |
| 权威源 | `src/components/ReDialog/{index.ts,type.ts,size.ts}`、`src/components/ReDrawer/` |

- 项目内已收敛模式（新弹窗照抄）：`addDialog({...}) + components/XxxForm.vue（reactive 表单 + getPayload() 校验）`，范例 `views/integration/knowledge/`、`views/integration/api-app/`。

### 2.3 图标：ReIcon / LocalIcon（全离线）

```ts
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
useRenderIcon(Delete)          // 编译期图标（unplugin-icons：~icons/ep/delete）
useRenderIcon("ep:user")       // 后端元数据下发的图标名 → LocalIcon 懒加载本地图标集
```

| 项 | 说明 |
|---|---|
| 渲染路径 | 字符串名统一走 `LocalIcon`：已注册直接渲染 → 内置集（`ep` / `ri` / `fa-solid`）懒加载本地 chunk → 未知只 DEV 告警，**绝不发在线请求** |
| 扩展方式 | 加内置集：`iconRegistry.ts::SET_LOADERS` 增条目；加随包图标：`offlineIcon.ts` 注册（`ep/xxx` 斜杠名给代码、`ep:xxx` 冒号名给服务端元数据，**双形态都注册**） |
| 权威源 | `src/components/ReIcon/src/{iconRegistry.ts,localIcon.ts,hooks.ts,offlineIcon.ts}` |

### 2.4 通用组件清单

| 组件 | 用途 | 关键点 |
|---|---|---|
| `RePlusSearch` | 下拉式表格选择器（el-select 内嵌 RePlusPage） | props：`api`（必需）/ `multiple` / `isTree` / `valueProps`（取值 / 展示字段）/ `listColumnsFormat` 等列格式化出口 |
| `RePureTableBar` | 表格工具栏（列显隐/密度/全屏/刷新） | RePlusPage 已内置，独立页面可单用 |
| `ReAuth`（全局注册为 `Auth`） | 按钮级权限 `<Auth value="create:CrmCustomer">` | 无 `v-auth` 指令 |
| `ReSegmented` | 分段控制器 | `renderBooleanSegmentedOption` 用于 boolean 表单项 |
| `ReCol` / `ReText` | 栅格 / 省略 Tooltip | — |
| `ReCropper` / `RePictureUpload` | 图片裁剪 / 头像裁剪上传 | — |
| `ReMfaConfirm` | 412 敏感操作二次验证弹窗 | `confirmMfa()`，http 层自动接线 |
| `ReSplitPane` | 可拖拽分栏（宽度持久化） | 配合 `src/hooks/useSplitPaneConfig.ts` |
| `ReCountTo` / `ReFlicker` / `ReQrcode` / `ReImageVerify` / `ReSendVerifyCode` / `ReTypeit` / `ReTreeLine` / `ReAnimateSelector` | 数字滚动 / 闪烁点 / 二维码 / 图形验证码 / 验证码倒计时 / 打字机 / 树形连接线 / 动画选择器 | 按需引用 |

### 2.5 请求层：BaseApi / http

```ts
// 1) 零定制：一行
export const bookApi = new BaseApi("/api/demo/book");
// 2) 带自定义动作
class KnowledgeApi extends BaseApi {
  syncRepo = () => this.request<DetailResult>("post", {}, {}, `${this.baseApi}/sync-repo`);
}
```

| 组件 | 职责 |
|---|---|
| `BaseApi` | 方法面 = 后端内建 Action 全集（list/create/retrieve/update/partialUpdate/destroy/batchDestroy/choices/columns/fields/import*/export*/recycle*） |
| `ViewBaseApi` | 单对象视图（无 list） |
| `http`（`src/utils/http/`） | 单例：Token 无感刷新（单飞 + 请求排队）、路由级取消（`skipRouteCancel` 豁免）、错误策略表（412 审批/MFA）、blob 下载、FormData 协议 v1 |
| `listRows(res)` | `data.results` 唯一拆包点 |
| `fetchAllRows(api)` | 自动翻页拉全量（下拉/树形列表用，防 100 条截断） |

- 契约：响应壳 `code=1000`；HTTP 400 的响应体会被 reject（供表单内联 `errors`）。
- 权威源：`src/api/base.ts`、`src/utils/http/`。

### 2.6 路由与权限

| 项 | 说明 |
|---|---|
| 静态路由 | `src/router/modules/*.ts` 自动收集（`remaining.ts` 不进菜单） |
| 动态路由 | 登录后 `GET /api/system/routes` → `handleAsyncRoutes`；`meta.frameSrc` → iframe 容器；component 字符串按 `/src/views/**` 匹配（未匹配 DEV 报错） |
| meta 约定 | `title/icon/showLink/auths/hiddenTag/dynamicLevel/fixedTag/frameSrc/rank/showParent/extraIcon/activePath/watermark`（路由侧另有 `keepAlive`） |
| 权限判定 | `hasAuth("动作:组件名")`；`usePageAuth(["customAction"])` 一次生成 RePlusPage 的 `auth` 对象；模板 `<Auth value="...">` |
| 约定 | 组件 `name` 与权限码后缀一字不差；无 `v-auth` |
| 权威源 | `src/router/utils/auth.ts`、`src/router/utils/async-routes.ts`、`src/layout/types.ts` |

### 2.7 状态管理（Pinia）

| store | 职责 |
|---|---|
| `pure-user` | 用户信息 / Token / 登出 / WS 消息处理（含桌面通知分派） |
| `pure-permission` | 动态菜单、按钮权限（`permissionAuths`）、keepAlive 缓存清单 |
| `pure-multiTags` | 多标签页（打开/关闭/缓存写盘） |
| `pure-app` / `pure-setting` / `pure-epTheme` | 布局 / 框架设置 / 主题色 |
| `pure-site-config` | 站点设置（实时保存、即时生效） |

- 约定：业务页面状态优先**组件本地 state**；需要跨页共享才进 store（新增 store 走 `modules/` + `useXxxStoreHook`）。
- 权威源：`src/store/modules/`（七个模块）。

### 2.8 工具库（`src/utils/`）

| 工具 | 用途 |
|---|---|
| `src/utils/dict.ts` | `useDict(code)` / `getDictItems(code)` 字典消费；`dictTagProps` / `statusTagProps` 状态标签统一入口 |
| `src/utils/aes.ts` | 请求体加密（v2 WebCrypto，自动回退旧格式） |
| `src/utils/sse.ts` | SSE 流式消费（fetch + ReadableStream，`parseSseBuffer` 纯函数） |
| `src/utils/websocket.ts` | WS 封装（心跳/重连） |
| `src/utils/download.ts` / `src/utils/message.ts` | 下载 / 消息提示（含读屏播报） |
| `src/utils/watermark.ts` / `src/utils/tree.ts` / `src/utils/form.ts` | 水印 / 树工具 / 表单序列化（FormData v1） |

- 权威源：`src/utils/dict.ts`、`src/utils/http/`、`src/utils/`。

### 2.9 指令与全局能力

| 指令 | 用途 |
|---|---|
| `v-copy` | 点击/指定事件复制 |
| `v-longpress` | 长按触发 |
| `v-ripple` | 水波纹 |
| `v-loading` | Element Plus 加载（`src/plugins/elementPlus.ts` 注册） |

新增全局组件登记点：`src/plugins/elementPlus.ts`（新增 `el-*` 用法**必须登记**，有单测比对清单）。

- 权威源：`src/directives/`、`src/plugins/elementPlus.ts`。

### 2.10 国际化（locale）

- 唯一文件：`locales/zh-CN.yaml`（源语言，eager）+ `locales/en.yaml`（懒加载），**必须成对补 key**（守护测试 `src/tests/locale-keys.spec.ts` 双向比对）。
- 列 label 回退链：`{localeName}.{key}` → `commonLabels.*` → 后端 gettext label（后端字段名在前端补 key 即可覆盖）。
- 菜单标题用 `menus.xxx` key；yaml 重复 key 会白屏。
- 权威源：`locales/zh-CN.yaml`、`locales/en.yaml`、`src/tests/locale-keys.spec.ts`。

