# ADR-007: FormData 多部分字段展开协议 v1（与 axios formSerializer 兼容）

- 状态：已接受（2026-09-08，TD-19 决策）
- 关联：TD-19

## 背景

前端所有 multipart/form-data 上传（`src/api/base.ts` 的 `hasFileObject` 路径：表单 + 文件的混合请求，如图片列表、富文本、附件字段）依赖 axios 的 `formSerializer: { indexes: null, dots: true }`（`src/utils/http/index.ts`）把嵌套对象序列化成点分键：

```
{ covers: [{ value: '2', label: '1111', pk: '2' }] }
  → covers.0.value=2&covers.0.label=1111&covers.0.pk=2
```

服务端 `common/drf/parsers/axios_form_data.py`（`AxiosMultiPartParser.format_data`）再把点分键还原为嵌套 dict / list（同时处理顶层 `pks` 批量键的 `getlist`）。两个端点强耦合："前端会用 axios 的这个配置" 是唯一契约，无文档、无单测，更换 HTTP 库时缺少迁移边界。

## 备选方案

1. **彻底解耦**：multipart 请求改为「单字段 JSON 信封」（如统一 `__data__=<json>`），后端解析该字段。
   - 成本：所有含嵌套字段的上传路径改写 + 后端 parser 重写 + 导入导出/既有数据兼容处理，动中层链路，风险高；
   - 收益：与 HTTP 库完全无关，但引入信封字段本身也是新协议，且破坏现有格式兼容（undo/回滚困难）。
2. **保持现状**：依赖"前端就是 axios"这一隐含事实，不动作。
   - 风险不消除：格式无契约、无测试，任何一侧改动都可能无声破坏另一侧；换库场景成本不可控。
3. **协议化收敛（采纳）**：把点分键格式定为**公开协议 v1**，前端收敛到单一序列化工具并文档化，后端 parser 补协议单测；技术选型仍用 axios，未来换库只需按协议 v1 编码。

## 决策

**方案 3**。理由：

1. 该键格式（`.` 分层、数字段=数组下标、顶层 `pks` 批量）与 qs 的 `{ allowDots: true, arrayFormat: 'indices' }` 输出一致（axios `formSerializer` 正是依赖 qs），本身就是成熟稳定的通用约定，没有理由弃用；
2. 真正的债务不是"axios"而是**契约不存在**：协议文档化 + 前后端各一处收敛点 + 服务端单测，即可用最低成本消除耦合风险；
3. 备选 1 的 JSON 信封同样是新协议却要破坏现状，属"为换而换"。

### 协议 v1 规范（权威定义见本文档 + `common/drf/parsers/axios_form_data.py` 头部注释）

- 键由 `.` 分隔层级：`a.b` → `{a: {b: ...}}`；
- 纯数字段表示数组下标：`a.0.b` → `{a: [{b: ...}]}`，下标不连续时按已出现顺序补齐；
- 顶层 `pks` 键一律按多值处理（`getlist`，批量删除/批量操作）；
- 其余顶层 key 为普通单值；
- 文件字段随 multipart 由 Django 侧照常解析，与键展开互不影响。

### 实施项（随功能迭代逐步落地，不设独立大任务）

- [ ] 前端：`src/utils/http/index.ts` 的 `formSerializer` 提取为命名常量并注释指向协议 v1（唯一收敛点）；
- [ ] 前端：新增 `src/utils/form/dataSerialize.ts` 封装"对象 → 点分 FormData"（基于现有 axios 配置语义，供 upload 路径显式调用，降低对 axios 隐式行为的依赖）；
- [ ] 后端：新增 `tests/unit/common/` 下 `AxiosMultiPartParser.format_data` 协议单测（嵌套 dict / 数组补齐 / pks 批量 / 空值）；
- [ ] 文档：`server/docs/schema/form-data-v1.md` 沉淀协议细节（键规则示例 + 边界行为）。

### 明确不做

- 不引入 JSON 信封字段（备选 1 被否）；
- 不替换 axiso 上传机制 / 不迁移 URLSearchParams；
- 不改 `AxiosMultiPartParser` 类名与导入路径（避免无谓 churn）。

## 替代方案裁决

| 方案 | 改动面 | 风险 | 契约 | 结论 |
|------|--------|------|------|------|
| 1 信封 | 全链路 | 高 | 新协议 | 否 |
| 2 保持现状 | 无 | 中（无契约） | 隐式 | 否 |
| 3 协议化收敛 | 低（文档+测试+收敛点） | 低 | 显式 v1 | **采纳** |

## 后果

- 正面：上传协议获得显式契约与回归测试；换 HTTP 库 / 手写 FormData 时只需满足 v1 键规则；前后端边界可独立演进。
- 负面：协议 v1 仍是"dot/index"约定而非 JSON Schema 级描述，复杂结构（对象数组混合）的表达能力有上限（当前业务字段形态已覆盖，未见缺口）。