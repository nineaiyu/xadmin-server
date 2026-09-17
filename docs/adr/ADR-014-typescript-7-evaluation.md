# ADR-014：TypeScript 7（原生编译器）升级评估

- 状态：**暂不升级**（评估完成，2026-09-11；重评条件见文末）
- 关联：原排期《剩余任务排期-2026.09-2027.02》D1「TS 7 前向评估（TS 6 升 7 或双版本）」（该文档已随 2026-09-12 规划清理删除）；
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

## 复审记录（2026-09-12，下期 W5）

- 现状实测：typescript **7.0.2** 已正式发布（原生 Go 实现）；vue-tsc 最新 **3.3.11**
  搭配 TS 7.0.2 直接失败：`ERR_PACKAGE_PATH_NOT_EXPORTED`（TS 7 的 package exports
  不再暴露 `./lib/tsc`，vue-tsc 3.x 的 tsc 驱动方式失效）；
- 结论：**维持暂不升级**；触发条件更新为「vue-tsc 发布声明支持 TS 7 原生版」，
  届时在独立分支用本仓 typecheck（tsc --noEmit && vue-tsc --noEmit）+ vitest 复测；
- 另注：项目当前已运行 typescript 6.0.3（TS 6 线），无阻塞问题。

## 复审记录（2026-09-15，下一年度规划 W3–W4 依赖窗口评估）

- 项目仍运行 typescript 6.0.3 + vue-tsc（本轮 AI 动作 E2E/门禁全绿，无类型层阻塞）；
- TS 7 升级触发条件不变：**vue-tsc 发布声明支持 TS 7 原生版**（此前实测 3.3.11 直接
  `ERR_PACKAGE_PATH_NOT_EXPORTED`）；未命中前维持暂不升级，届时独立分支复测
  `tsc --noEmit && vue-tsc --noEmit` + vitest。

## 复审记录（2026-09-16，第四年度 2030-05 前端跟随窗口）

- 第三次复核：typescript **7.0.2**（npm 最新）+ vue-tsc **3.3.11**（当前最新）本仓实测
  仍不兼容——`ERR_PACKAGE_PATH_NOT_EXPORTED`（TS 7 的 package exports 不再暴露
  `./lib/tsc`，vue-tsc 3.x 的 tsc 驱动方式失效）；触发条件未满足；
- 已回滚至 typescript 6.0.3 并复测 typecheck 全绿（exit 0）；
- 同期完成：dev 工具链 major 升级（cssnano 9 / postcss-import 17 / unplugin-icons 24）+
  `@vueuse/core 14→15`（20 文件使用面，typecheck / 250 单测 / 生产构建全绿）；
  包体基线收紧至 **522 KB**（消除 35.5 KB 历史余量）；
- 维持暂不升级；触发条件不变。

## 复审记录（2026-09-17，第九年度独立分支试点）

- npm 实查：typescript latest **7.0.2**（next 7.1.0-dev，新 API 未稳定）；vue-tsc 最新仍 **3.3.11**，
  peer 未排除 7.x 但**未发布 TS 7 支持声明**——触发条件未命中；
- 包名修正：§四.3 所引并存包 `@typescript/native` 实查 npm **不存在**（404）；
  `@typescript/typescript6` 存在（6.0.2 止，bin `tsc6`）；
- 独立 worktree（dev HEAD，分支试点后已清理）实测：
  - CLI 段：TS 7.0.2 `tsc --noEmit` **1.15s 通过** vs TS 6.0.3 1.28s——§一预期的 ~5x 提速
    在本仓**不成立**（tsc 段仅占 typecheck 总时长 ~1s，大头在 vue-tsc 段 10s 量级）；
  - 整体升 7：vue-tsc 第 4 次复现 `ERR_PACKAGE_PATH_NOT_EXPORTED`（exit 1）；
  - 并存方案（`typescript@^6.0.3` + `typescript7: npm:typescript@7.0.2` 别名共存）：
    typecheck / strict / vitest 273 全绿，接线方式 `node node_modules/typescript7/bin/tsc` 可行；
- 结论：**并存方案收益不足**（CLI 段节省 ~0.1s vs 新增原生二进制 devDep 与 CI 安装成本），
  **维持 `typescript ^6.0.3` 单版本**，别名接线不落地；
- 触发条件不变：**vue-tsc 发布声明支持 TS 7 原生版**（预计随 TS 7.1 稳定释出），
  届时整体升级复测；季度复核随依赖窗口滚动。
