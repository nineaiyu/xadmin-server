# 元数据契约 Schema（T2.3）

本目录是 search-columns / search-fields 元数据接口的 JSON Schema 事实源：
`tests/unit/common/test_metadata_schema.py` 在 CI 中持续校验真实接口响应与
Schema 的一致性。

## 契约镜像关系

- client 仓库 `contract/schema/` 是本目录的**镜像副本**，用于前端
  `pnpm gen:metadata-types` 生成 TS 类型及其 CI regen-diff 门禁
  （CI 只 checkout client 仓库，无法跨仓读取本目录）。
- **变更流程**：修改本目录 Schema（破坏性契约变更，需评审）→
  同步镜像到 client `contract/schema/` → client 跑 `pnpm gen:metadata-types`
  并提交生成的 `src/api/types/*.d.ts`。
