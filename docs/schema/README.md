# 协议契约 Schema

本目录是前后端协议契约的 JSON Schema 事实源，由 `tests/unit/common/` 在 CI 中
持续校验真实响应与 Schema 的一致性：

| Schema | 契约 | 校验测试 |
|--------|------|----------|
| `search-columns.schema.json` / `search-fields.schema.json` | 元数据接口（RePlusPage 渲染契约，T2.3） | `test_metadata_schema.py` |
| `api-response.schema.json` | 统一响应信封（`common/core/response.py`） | `test_contract_schemas.py` |
| `routes-payload.schema.json` | 动态路由接口完整载荷（路由树 + auths 权限码） | `test_contract_schemas.py` |
| `ws-frame.schema.json` | WebSocket 消息协议 v1 帧（`message/protocol.py`） | `test_contract_schemas.py` |

## 契约镜像关系

- client 仓库 `contract/schema/` 是本目录的**镜像副本**，用于前端
  `pnpm gen:metadata-types` 生成 TS 类型及其 CI regen-diff 门禁
  （CI 只 checkout client 仓库，无法跨仓读取本目录），
  并由 `pnpm check:contract` 校验镜像与真源一致。
- **变更流程**：修改本目录 Schema（破坏性契约变更，需评审）→
  在 client 仓库跑 `pnpm sync:contract`（一键同步镜像到 `contract/schema/` +
  重新生成 `src/api/types/*.d.ts`）→ 连同生成物一起提交。
  单仓检出（无服务端目录）时可用 `XADMIN_SERVER_DIR` 指向服务端仓库；
  CI 仍以 `pnpm check:contract` 校验镜像未被绕过手工修改。
