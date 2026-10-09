# xadmin 基线指标看板（metrics.md）

> 建立日期：2026-09-04（半年规划 T1.8 交付物）
> 用途：半年规划 §八 KPI 的基线登记处，每阶段结束时回填实测值。
> 统一口径：any 统计正则 `: any|as any`（src/ 内）；跨 app 坏味道 = 非 tests/migrations 的跨 app `models/serializers/views/notifications/backends/signal` 直接 import。

> **本文为概览页**：KPI 基线与构建 / 体积见下；
> 逐阶段回填履历（§三 回填记录 + 依赖安全审计）见 [metrics-history.md](metrics-history.md)，
> 性能与前端基线实测履历（§五 性能基线及后续日期条目）见 [metrics-perf-history.md](metrics-perf-history.md)。

## 一、基线实测值（2026-09-04）

| 指标 | xadmin-server | xadmin-client |
|------|--------------|---------------|
| 单元测试用例数 | 184（本次实测，含 T1.1 新增 6 例） | 62 |
| 覆盖率 | **61%**（9,425 语句 / 3,681 未覆盖，实测） | 仅统计 api+utils，无门禁 |
| 覆盖率门禁 | CI `--cov-fail-under=55`（历史基线；当前门禁值见 `.github/workflows/test.yml`，2026-09-18 起为 85%） | 无 |
| E2E 用例 | 34（双浏览器 chromium+webkit，retries 1，本地全绿 2026-09-06） | 34（CI 实跑：68 passed / 7.1m，dev push 触发，2026-09-06） |
| any 存量 | — | 226 处 / 91 文件 → **0 处**（T1.7 五期/TD-23 后，2026-09-05 实测；grep 口径 `: any|as any` 全仓归零） |
| any 豁免清单 | — | **0（四期全部清零摘除，2026-09-05）**；历史路径 71→40→36→34→…→14→0；注意 eslint 规则块仅挂 *.ts/tsx，*.vue 不受 no-explicit-any 约束（缺口登记 TD-23） |
| 跨 app 坏味道 import | **10 处**（T1.5 后，原 21）→ **4 处**（T2.2 后，2026-09-05 实测：管理命令×3 + demo FK×1，均为规划允许保留，CI 门禁已接入） | — |
| 巨型文件（>500 行，非数据/迁移） | modelset.py 749 → **0**（T2.1 拆分为 10 模块包，最大 metadata.py 287 行） | lay-tag 695 / lay-setting 680 / user hook.tsx 603 / handle.tsx 789 → **0**（T2.5 + N3 五期 F5 拆分：handle.tsx → handle-dialog 319 / handle-record 209 / 本体 273，导出签名不变） |
| TODO/FIXME 标记 | 0 | 0 |

## 二、构建与体积（client）

### T3.4 分包后（2026-09-05 实测，gzip9 口径）

| 指标 | 基线（09-04） | T3.4 后 | 备注 |
|------|------|------|------|
| **首屏 JS（静态依赖闭包合计）** | 31 chunks / **1,020.5 KB** | 12 chunks / **905.0 KB（-11.3%）** | 旧口径 698.8KB 仅统计入口单文件，漏算 26 个静态依赖块，已修正 |
| 主 chunk `index-*.js` | 2,089 KB raw / 698.8 KB gzip | 459.9 KB raw / **440 KB gzip（-37%）** | 应用代码；vendor 经 advancedChunks 分离 |
| `vue-core` / `element-plus` / `plus-pro` / `echarts` | 混在主 chunk | 87 / 279 / 20 / **懒加载** KB | echarts 改启动预热+动态加载，仅仪表盘消费 |
| 移出首屏的大块 | — | descriptions 110K、RePureTableBar 39K、checkbox 30K 等 6 个 | |
| `vanilla-jsoneditor-*.js` | 1,185 KB raw / 347.8 KB gzip | 不变（懒加载） | 已知懒加载大件，仅 JSON 编辑表单触发 |
| `index-*.css` / `element-plus-*.css` | 508 KB raw / 73.3 KB gzip | 拆分缓存 | 全量 EP CSS 仍在首屏（element-plus 按需引入为 -20% 后续主路径） |
| chunkSizeWarningLimit | 4000（掩盖膨胀） | **1000** | vanilla-jsoneditor ~1.2MB 会告警，为有意保留的信号 |
| 构建耗时 | 见 CI（本地未单测） | 同左 | |

## 四、与半年规划 KPI 的对照

| KPI（§八） | 基线 | 目标（2027-02） |
|------------|------|-----------------|
| server 覆盖率门禁 | 55%（实测 61%） | 75% |
| server 用例数 | 184 | 260+ |
| client 用例数 | 62 | 120+ |
| E2E 入 CI | 0 | 15+ |
| 跨 app 坏味道 | 10 | ≤5 |
| any 豁免文件 | 71 | 0 |
| 首屏主 chunk gzip | 698.8 KB（旧口径）→ 真实首屏 1,020.5 KB | -20% | **主 chunk 440 KB（-37%）✓；真实首屏 905 KB（-11.3%）**，剩余路径：element-plus 按需引入 |
| pip/pnpm audit 高危 | 周检中 | 0 |

## 五、AI 观测与成本口径（登记）

| 维度 | 现状 |
|------|------|
| 「量」观测 | AI 用量账本 `AiUsageRecord` 逐次记账（链路 / 档案 / 模型 / token / 耗时 / 成败）；汇总端点 `GET /api/ai/assistant/usage` 提供按天 / 链路 / 档案 / 模型分布 + 成功率与延迟（均值恒给，P95 样本足够才给），另 `GET /api/ai/assistant/metrics` 为 OperationLog 视角的调用观测 |
| 「钱」成本 | **数据面无任何价格字段**：`AiProfile` 无单价、`AiUsageRecord` 无成本列。成本核算形态已固化预研并登记为**触发制**（[ADR-076](adr/ADR-076-ai-cost-accounting.md)），触发条件满足前不实现，无成本汇总口径 |

> 登记链路：成本维度（单价表 / 记录级成本快照 / 成本汇总）的触发条件、预研形态与边界见 ADR-076；本表仅登记「数据面无价格字段」这一事实基线。
