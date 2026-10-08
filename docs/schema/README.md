# 协议契约 Schema

本目录是前后端协议契约的 JSON Schema 事实源，由 `tests/unit/common/` 在 CI 中
持续校验真实响应与 Schema 的一致性：

| Schema | 契约 | 校验测试 |
|--------|------|----------|
| `search-columns.schema.json` / `search-fields.schema.json` | 元数据接口（RePlusPage 渲染契约，T2.3） | `test_metadata_schema.py` |
| `api-response.schema.json` | 统一响应信封（`packages/xadmin-common/common/core/response.py`） | `test_contract_schemas.py` |
| `routes-payload.schema.json` | 动态路由接口完整载荷（路由树 + auths 权限码） | `test_contract_schemas.py` |
| `ws-frame.schema.json` | WebSocket 消息协议 v1 帧（`message/protocol.py`） | `test_contract_schemas.py` / `test_ws_frame_schema.py` |

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
- **例外（生成物）**：`ws-frame.schema.json` 是生成物，禁止手工编辑——真源为
  `message/protocol.py`（Action 枚举与 Payload TypedDict）与
  `message/ws_schema.py`（payload 的 required / 描述声明）；改完真源后跑
  `python scripts/gen_ws_frame_schema.py` 重新生成（`--check` 可校验漂移），
  守护测试 `test_ws_frame_schema.py` 保证「落盘 == 渲染」与字段集合对账。
- **input_type 词表（ADR-083，稳定公共契约）**：两份元数据 Schema 的
  `input_type` 属性 = 封闭核心枚举 ∨ `^api-` 前缀族（分支显式 `type: string`，
  生成 TS 类型收敛为 `string` 开放边界）；`x-fallback-rendered` 自定义关键字
  登记无内置渲染器的回退呈现类型。**单一事实源为服务端
  `packages/xadmin-common/common/core/modelset/input_types.py`（`DECLARED_INPUT_TYPES` 等）**，
  与本目录枚举锁步对账（`test_metadata_schema.py`）；client 侧
  `metadata-vocabulary.spec.ts` 做词表 ⇄ 渲染器注册表双向覆盖对账。
  新增类型的扩展流程见 ADR-083（词表 → Schema → sync:contract → 注册表）。
