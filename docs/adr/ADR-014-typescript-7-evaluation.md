# ADR-014：TypeScript 7（原生编译器）升级评估

- 状态：**暂不升级**（评估完成，2026-09-11；重评条件见文末）
- 关联：排期《剩余任务排期-2026.09-2027.02》D1「TS 7 前向评估（TS 6 升 7 或双版本）」；
  前端仓库 `xadmin-client`（`typescript ^6.0.3` + `vue-tsc ^3.3.11`）
- 结论：TS 7 对本仓 **CLI 侧已可用且显著更快**（`tsc --noEmit` 0.6s vs TS 6 约 3s），
  但 **Vue SFC 类型检查链路（vue-tsc）与 TS 7 不兼容**——TS 7.0 不提供程序化 API，
  官方明确 Volar 系（Vue/Svelte/Astro/MDX）应继续用 TS 6，等 TS 7.1 新 API。
  因此本期维持 TS 6.0.3，并在下文记录实测数据与重评触发条件。

## 一、背景

TypeScript 7.0（2026-07-08 发布）是用 Go 重写的原生移植版，官方称全量构建快 8–12x。
本仓前端 gate 是 `pnpm typecheck = tsc --noEmit && vue-tsc --noEmit --skipLibCheck`，
两条链路对 TS 的依赖方式不同（CLI vs 程序化 API），必须分别评估。

## 二、实测数据（隔离 git worktree，2026-09-11）

环境：Node 24.20.0、pnpm 11.25.0；在 `xadmin-client` HEAD 的独立 worktree 中
`pnpm install --frozen-lockfile` 后换装 `typescript@7.0.2`（不改动主工作区）。

| 项 | TS 6.0.3 | TS 7.0.2 |
|---|---|---|
| `tsc --noEmit`（.ts/.tsx 部分） | 通过（约 3.3s，含 6 处既有报错） | **通过，0.6s**（≈5x 提速） |
| `vue-tsc --noEmit`（.vue SFC 类型检查） | 通过 | **失败：`ERR_PACKAGE_PATH_NOT_EXPORTED`**（解析 `typescript/lib/tsc.js`） |
| 组合 `pnpm typecheck` | 通过（9.2s） | 失败（vue-tsc 阶段） |

`vue-tsc` 最新版为 3.3.11（与本仓一致），peer 仅声明 `typescript >=5.0.0`，
但运行时依赖 TS 5/6 的 JS 入口布局，TS 7 已不再提供 → 运行期硬失败（非类型错误）。

官方发布说明（Announcing TypeScript 7.0）已明确该边界：

- **7.0 不附带程序化 API**，新 API 在 7.1；依赖 API 嵌入 TS 的工具（Volar 系 Vue/Svelte/
  Astro/MDX、Angular 模板检查、webpack loader、typescript-eslint 等）暂时只能用 6.0；
- 并存方案：`@typescript/typescript6`（提供 `tsc6` 与 TS 6.0 API）+ `@typescript/native`
  （别名到 `typescript@^7.0.2` 拿新 `tsc`）；
- 迁移面：`target: es5`、`downlevelIteration`、`moduleResolution: node/node10`、`baseUrl`、
  `module: amd/umd/systemjs` 等变为硬错误；`types` 默认 `[]`、`rootDir` 默认 `./`。

## 三、本仓 tsconfig 的迁移面（已核对，风险低）

`xadmin-client/tsconfig.json` 现状与 TS 7 要求基本对齐：

| TS 7 变更 | 本仓现状 | 结论 |
|---|---|---|
| `baseUrl` 不再支持 | 未使用 `baseUrl`，`paths` 为相对项目根的 `./src/*` | 无需改动 |
| `moduleResolution: node/node10` 不支持 | `bundler` | 无需改动 |
| `types` 默认 `[]` | 显式列出 node/vite/client 等 5 项 | 无需改动 |
| `module: amd/umd/systemjs` 不支持 | `ESNext` | 无需改动 |
| `target: es5` 不支持 | `ESNext` | 无需改动 |
| `strict` 默认 true | 显式 `strict: false`（存量类型债收敛策略见注释） | 需复核：7.x 是否允许显式关闭 |
| `esModuleInterop` 不能为 false | `true` | 无需改动 |

即：**代码侧迁移成本很低，真正的前置是工具链（vue-tsc）就绪**。

## 四、决策与重评条件

1. **本期维持 `typescript ^6.0.3`**（不改 lockfile、不动 CI）；理由：vue-tsc 无替代
   （Vue 官方 SFC 类型检查唯一实现），无法只升 CLI 一侧；
2. **重评触发条件（满足任一即重评）**：
   - `vue-tsc` 发布声明支持 TypeScript 7 的版本（预计随 TS 7.1 新 API 释出）；
   - 或 Vue 官方给出 TS 7 下的 SFC 检查替代方案（如 Volary 系新语言服务）。
3. **届时推荐落地方式**（届时按实测调整）：并存方案先拿性能——`@typescript/native`
   提供 TS 7 的 `tsc` 跑 CLI 全量检查（约 5x 提速），`typescript` 仍别名到
   `@typescript/typescript6` 供 vue-tsc 使用；确认稳定后再评估是否统一到 7.x。
4. 观察项：CI 类型检查耗时（当前 typecheck 约 8–9s，升级后预期 ~4s 量级），
   以及 TS 7 是否引入新的硬错误（本仓 tsconfig 已核对，无需改动）。

## 五、附带修复（评估过程中发现）

评估时发现工作区 `pnpm typecheck` 存在 6 处 `TS2339`（`auth.approve` 等自定义权限
码不存在于 `reactive` 推断类型上）——根因是自定义权限码未按项目既有范式
（demo/book、system/role）先声明默认值再展开，导致 `UnwrapNestedRefs` 丢失索引签名。
已按范式修复 `src/views/system/approval/instance/utils/hook.tsx`，typecheck 恢复全绿
（exit 0，8.4s）。记录在此：TS 升级评估顺带暴露并消除了类型 gate 的存量红灯。
