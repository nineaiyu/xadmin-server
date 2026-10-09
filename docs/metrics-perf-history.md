# 性能与前端基线实测履历（metrics-perf-history）

> 本文为 [metrics.md](metrics.md) 的历史子页，收录性能基线（§五）与各日期实测条目
> （首屏体积 / 体验基线 / 压测复测 / 尾延迟定案等）。KPI 基线与构建 / 体积见概览页；
> 阶段回填与依赖安全审计见 [metrics-history.md](metrics-history.md)。

## 五、性能基线（P3/T3.1，2026-09-06 首测完成回填）

> 流程与口径见 [ops/performance-baseline.md](ops/performance-baseline.md)；三轮中位数、k6 客户端口径（含本机回环）。

**环境元数据**：Apple Silicon macOS + OrbStack Docker；被测服务 gunicorn + UvicornWorker×4（生产同参，容器化，代码 = aefb7ce）；PostgreSQL 16.8 / Redis 7.4.3 专用一次性容器；种子 1000 个 `perf_` 用户 + 默认数据；k6 v2.0.0；压测容器加 `tcp_tw_reuse=1` + 扩大临时端口段以规避 **TD-25（ASGI 每请求新建 DB 连接）** 引发的端口耗尽——每请求连接开销保留在下列数据中，符合生产现状，TD-25 修复后全体数字将进一步改善。

| 接口 | 档位（默认） | RPS | P50 | P95 | 错误率 | 实测日期 | 环境 |
|------|-------------|-----|-----|-----|--------|---------|------|
| 1 登录（login/basic） | 5 VU / 30s | 31.3 | 159.1ms | 173.9ms | 0 | 2026-09-06 | 首测环境① |
| 2 菜单/路由（routes） | 20 VU / 1m | 682.0 | 26.8ms | 61.8ms | 0 | 2026-09-06 | 首测环境① |
| 3 列表页（user list） | 20 VU / 1m | 267.4 | 66.2ms | 147.5ms | 0 | 2026-09-06 | 首测环境① |
| 4 元数据（search-columns） | 20 VU / 1m | ≈34.8* | 46.4ms | 81.5ms | 0 | 2026-09-06 | 首测环境①② |
| 4 元数据（search-fields） | 20 VU / 1m | ≈34.8* | 43.4ms | 78.3ms | 0 | 2026-09-06 | 首测环境①② |
| 4 元数据（list?with_meta=1，T3.2 内联） | 20 VU / 1m | ≈34.8* | 97.6ms | 203.8ms | 0 | 2026-09-06 | 首测环境①② |
| 5 导出（export-data xlsx，username=perf_ 过滤） | 5 VU / 1m | 15.4 | 253.0ms | 632.6ms | 0 | 2026-09-06 | 首测环境① |
| 6 导入（import-data update 模式） | 5 VU / 1m | 130.8 | 39.1ms | 48.5ms | 0 | 2026-09-06 | 首测环境① |

\* 三变体同循环等比混跑（04 合计 104.5 RPS）；P50/P95 为分档 Trend 指标三轮中位数（k6 v2 移除 group 子指标，改用自定义 Trend 分档）。

**T3.2 对照结论**：内联单请求（P50 97.6 / P95 203.8ms）明显优于「分离列表+columns+fields 三请求串联」（P50 简单相加 ≈156ms、P95 ≈226ms，另有两次额外往返），配合首开请求数 -2，T3.2 优化收益得到基线数据支撑。

回填要求：记录环境元数据（机器规格 / gunicorn worker 数 / DB 引擎与版本 / 种子规模）；元数据 P95 需同时
登记分离请求与 with_meta=1 内联两组，用于对照 T3.2 目标（较基线 -50%，目标 <150ms）。

### 2026-09-14 AI 检索评测（A1，评测驱动门控）

评测集 `tests/data/ai_retrieval_eval.json`（36 问 + 期望出处，含 6 条改写式难题）随
`pytest tests/integration/ai/test_ai_retrieval_eval.py` 入 CI（含「期望出处有效性」守护与耗时护栏）。

| 指标 | 实测 | 门控 | 判定 |
|------|------|------|------|
| hit@5 | **35/36 = 97.2%** | < 75% 才升级向量 | 不升级（[ADR-037](adr/ADR-037-ai-retrieval-evaluation.md)） |
| 知识库分块数 | **535** | > 500 触发评估 | 略超（+7%），质量与时延均达标 → 维持 |
| 单次检索耗时 | 平均 30.3ms / 最差 31.6ms | P95 > 300ms 重估 | 达标 |

结论：词频重叠检索在现语料规模下质量充分，**不引入向量嵌入**；重开条件（hit@5<75% / 分块>1000 /
检索 P95>300ms / 明确语义检索需求）见 ADR-037。

### 2026-10-09 HNSW 向量定型实测（真实 embedding 档案 + 仓库语料）

重开条件（分块 > 1000）已由语料自然增长触发（1321 块），向量通道从「假向量测试」进入
「真实模型全链路」实测：

- **环境**：本机 compose（PostgreSQL 17 + pgvector），LM Studio 本地 `text-embedding-bge-m3`
  （Q8_0，1024 维），后端容器经 `host.docker.internal:1234` 调用（出站白名单
  `OUTBOUND_ALLOWED_HOSTS=host.docker.internal`），档案用途 `embedding`（`AiProfile`）。
- **语料**：`manage.py sync_ai_knowledge` 同步仓库文档（153 个 md）→ **1321 块**
  （超过 `INDEX_MIN_ROWS=1000` 门槛，进入定型窗口）。
- **构建与定型**：`manage.py build_ai_embeddings` 全量构建 **1321/1321（fresh，无 stale）**，
  构建成功回调自动执行 `ensure_vector_index` —— `column=vector(1024) hnsw=True dims=[1024]`；
  近邻查询 `EXPLAIN (ANALYZE)` 实测走 `Index Scan using aichunk_embedding_vector_hnsw`
  （`vector_cosine_ops`，m=16 / ef_construction=64，0.6ms）。
- **hit@5 对照（36 问评测集，同语料同时刻）**：

| 模式 | hit@5 | 平均耗时 | 最差耗时 |
|------|------|------|------|
| 纯词频（基线通道） | **35/36 = 97.2%** | 5.8ms | 131.3ms |
| 混合（bge-m3 向量 + 词频 RRF） | **35/36 = 97.2%** | 21.1ms | 54.8ms |

- **结论**：真实 embedding 全链路（档案 → 构建 → 自动定型 → HNSW 检索 → 评测）跑通；
  hit@5 与词频基线持平（唯一 miss 同为 `hard-field-hidden`，两通道均未召回首选出处），
  维持 ADR-065 口径——向量通道定位为语料规模化后的容量/语义补充通道，门控（≥75%）不变。
  混合模式平均耗时含 query 向量往返（约 15ms），最差耗时由词频全量扫描侧降至 54.8ms。

### 2026-09-12 首屏基线复测（下期规划 W3 收口）

| 指标 | 09-05 基线（T3.4 后） | 09-12 实测 | 变化 |
|------|------|------|------|
| **首屏 JS 静态闭包合计（gzip9，dist/index.html modulepreload 闭包）** | 12 chunks / 905.0 KB | 8 chunks / **861 KB** | **-4.9%**（-44 KB） |
| 主 chunk `index-*.js` | 440 KB | 466 KB | +26 KB（审批流二期 @vue-flow 画布/版本回滚等新应用代码入包） |
| `element-plus` | 279 KB | 234 KB | -45 KB |
| `echarts` | 懒加载 | 懒加载（346 KB 独立 chunk，不在闭包） | 维持 |
| 闭包其余构成 | — | vue-core 86 / i18n 51 / plus-pro 13 / 杂项 7 KB | — |

**结论（W3 收口）**：-10% 目标（815 KB）未达成。echarts 懒加载已生效、element-plus 已按需注册，闭包唯一实质杠杆是 index 应用代码（466 KB，含 ReIcon/data.ts 3,869 行图标数据）——**ReIcon 瘦身立项价值确认**，但预期收益需先按 data.ts 在闭包中的实际压缩占比评估后再决策（候选池口径不变）。

### 2026-09-12 ReIcon 瘦身实测（候选池触发条件核验，结论：不立项）

`src/components/ReIcon/data.ts` 实测 3,869 行 / 79.7 KB raw / **15 KB gzip9**，占首屏闭包（861 KB）的 3.2%；
唯一消费点为 `ReIcon/src/Select.vue`（菜单表单图标选择器）。整块剥离的收益上限为首屏 -1.7%，
且需把图标选择器异步化——**数据不支持立项，候选池该项关闭**（首屏优化的剩余空间不在数据文件）。

### 2026-09-14 首屏 KPI 改「增长预算制」+ wangeditor 拆包（W0 收口）

**KPI 新口径（替代绝对 -10%）**：每个窗口首屏闭包（gzip9，`dist/index.html` 静态依赖闭包）增长 **≤15 KB**；
超预算需在 PR 说明理由并刷新基线。执行器 = `scripts/check-bundle-size.mjs` + `scripts/bundle-size-baseline.json`，
已进 CI `.github/workflows/lint-code.yml`（`pnpm build && pnpm check:bundle-size`）。

| 指标 | 09-12 基线 | 09-14 拆包前复测 | 09-14 拆包后 | 变化 |
|------|-----------|-----------------|-------------|------|
| **首屏 JS 静态闭包（gzip9，9 chunks）** | 861 KB | 881.7 KB | **557.5 KB** | **-324.2 KB（-36.8%）** |
| 主 chunk `index-*.js` | 466 KB | 518.8 KB | **194.6 KB** | -62.5% |
| element-plus / vue-core | 234 / 86 KB | 235.6 / 86.4 KB | 235.6 / 86.4 KB | 维持 |

**拆包决策（数据来源：`pnpm analyze:bundle`，rollup-plugin-visualizer raw-data，renderedLength 口径）**：

| 候选 | 实测结论 | 动作 |
|------|----------|------|
| `@wangeditor`（编辑器栈） | **1044.8 KB rendered 落在主 chunk**（App.vue 静态 `Boot.registerModule` 把插件+核心拖入入口闭包，与 renderers-form 注释「编辑器全懒加载」的设计意图相反） | **立项并交付**：注册迁至 `src/utils/wangEditorBoot.ts`（幂等懒注册，含 rolldown UMD 解包兼容），WangEditor.vue / NoticeShow.vue 改「先 await 注册、再加载编辑器组件」的异步组件 |
| `version-rocket`（版本更新提示） | 129.7 KB rendered 在主 chunk（含 119.4 KB 主题） | **立项并交付**：App.vue 改动态 `import()`，5 分钟轮询行为不受影响 |
| `@vue-flow` | 已在独立懒加载 chunk（FlowCanvas 50.2 KB gzip，不在闭包） | 确认无需动作 |
| `plus-pro-components` | 闭包内仅 13.2 KB gzip（97 KB rendered） | 收益低于预算量级，不立项 |
| ReIcon/data.ts | 15 KB gzip，占闭包 3.2% | 维持既有「不立项」结论 |

**剩余杠杆（登记为后续候选，均未达立项阈值）**：i18n zh/en 语言包 ~49 KB gzip（需 i18n 懒加载改造，影响面大）、
`@zxcvbn-ts` 26 KB、`vue-tippy` 24 KB、`sortablejs` 17 KB（3 处消费需动态导入）、`vue-json-pretty` 9.6 KB。

**验证**：拆包后主链路 E2E（notice 创建/编辑、NoticeShow 只读）双浏览器通过；`pnpm analyze:bundle` 复测确认
闭包内不再含 wangeditor chunk。

### 2026-09-15 首屏三期余量（i18n / sortablejs / vue-json-pretty，W7–W10 收口后的候选池项）

基线口径：560.6 KB（W7–W8 收口后）→ 本轮实测 **519.6 KB（-41.0 KB / -7.3%）**，主 chunk 151 → 130.5 KB。

| 项 | 改造 | 结果 |
|------|------|------|
| `sortablejs`（列拖拽排序） | `RePureTableBar/bar.tsx` 静态 import 改为「列排序」交互触发时动态 `import()`（类型经 `import type` 保留） | 移出闭包 |
| `vue-json-pretty`（JSON 展开 + 样式） | `RePlusPage/utils/renderers-detail.tsx` 改 `defineAsyncComponent` + 动态 import CSS（Vite 产出独立 css chunk，加载时注入） | 移出闭包（JS + CSS 双份） |
| i18n 语言包（en.yaml + element-plus en locale） | `plugins/i18n.ts`：zh-CN 保持 eager（默认语言/源语言，`flatI18n` 同步探测依赖）；en 由 `ensureLocale()` 按需加载（`app.mount` 前按初始语言 + 语言切换处 + `watch` 兜底防止 key 泄漏），新增 `e2e/locale.e2e.ts` 双浏览器守护（切换往返 / 落库后刷新首屏英文） | **-20.8 KB**（540.4 → 519.6） |
| `@zxcvbn-ts` | 复测：已在独立懒 chunk（3 处均为懒视图消费），**与候选池登记值（在闭包）不符 → 无需动作** | 无变化 |
| `vue-tippy`（~24 KB gz） | 评估：`app.use(VueTippy)` + 指令式用法散布布局层（侧栏 tooltip 属首屏交互），移出闭包需重写指令为异步实现或降级 tooltip | **评估后维持**（收益低于风险，登记同 ReIcon 范式） |

已核实为懒加载 / 不在闭包（无需动作）：echarts、wangeditor、version-rocket、`@vue-flow`。

### 2026-09-17 前端体验基线首测（U1，chromium + dev 链路）

| 页面 | TTFB | FCP | LCP | CLS |
|------|------|-----|-----|-----|
| 登录页（冷加载） | 332 ms | 1172 ms | 1224 ms | 0.016 |
| 首页（登录后整页重载） | 3 ms | 284 ms | 284 ms | 0.020 |
| 用户列表（登录后整页重载） | 3 ms | 268 ms | 268 ms | 0.048 ~ 0.240（波动） |

- 采集：`pnpm test:e2e:perf`（刷新基线加 `:update`）；基线文件 client `e2e/perf-baseline.json`，
  按「平台-CI」分组存放（数字随机器变化，**仅同环境趋势可比**）；
- 口径：dev 链路（vite）+ 本地机器；现阶段只做「离谱回归」兜底（TTFB ≤ 2s / LCP ≤ 5s / CLS ≤ 0.5），
  正式预算待数据积累后评审（U1 阶段一目标：先立基线后定预算）；
- 发现：用户列表页 CLS 在 0.048 ~ 0.240 间波动（异步数据填充引起），高于 0.1「良好」线
  → 登记体验改进候选（待稳定口径复测后再评估）。

### 2026-09-18 列表页 CLS 收口（U1，修复后复测）

| 页面 | TTFB | LCP | CLS（修复前 → 修复后） |
|------|------|-----|----------------------|
| 用户列表（登录后整页重载） | 33 ms | 276 ms | 0.2403（波动 0.048~0.240）→ **0.0214** |
| 登录页（冷加载） | 327 ms | 1320 ms | 0.0153（页面级小位移，维持） |
| 首页（登录后整页重载） | 72 ms | 308 ms | 0.0141（页面级小位移，维持） |

- 根因（`cls_top` 几何明细定位，2026-09-18）：①搜索列元数据（列表首包 `with_meta=1`）
  到达前搜索卡片只渲染按钮行（56px），到达后叠加字段行（+50px），把下方表格/分页整体推移；
  ②el-table 列宽在 `requestAnimationFrame(doLayout)` 内才落位，列挂载后第一帧仍是浏览器
  均分宽度（长表头换行，实测表头 155px → 41px、行高 86px → 53px）——两件事同帧，
  中间帧被真实绘制产生大位移（单次 shift 占 CLS 的 88%）；
- 修复（client `RePlusPage`）：搜索卡片 `min-height` 高度占位（元数据到达前后卡片高度一致）
  + 列集合变化时隐藏表格两帧（`visibility: hidden` 不参与 layout-shift 统计）；
- 剩余位移为登录页表单/布局页脚等页面级小项（各 ≤ 0.008），登记后续候选。

### 2026-09-29 首屏体积门禁分账（契约兑现批，P1-6.4-3）

| 指标 | 基线（2026-09-23） | 当前 | 增量 / 预算 |
|------|------|------|------|
| 首屏闭包（gzip9，总口径） | 537.4 KB | **551.6 KB** | +14.2 KB（**仅供参考，不再作判定**） |
| 其中代码账本 | 481.4 KB | **487.3 KB** | +5.9 KB / 15 KB ✓ |
| 其中 i18n 账本（zh 语料 + vue-i18n 分组） | 55.9 KB | **64.3 KB** | +8.4 KB / 15 KB ✓ |
| 主 chunk `index` | 134.9 KB | 141.5 KB | +6.6 KB（定位用，不作独立门禁） |

- 动因：总口径 +14.2/15 KB 已近打爆，而增长里 i18n 语料占 8.4 KB（词条随功能同步增长，
  属产品资产）——与代码增量混在一个预算里会互相挤占（语料增重把代码预算吃光）；
- 口径：`scripts/check-bundle-size.mjs` 把闭包拆「代码」与「i18n」两账本（i18n = chunk 名以
  `i18n` 开头，即 zh 语料 + vue-i18n 运行时分组），各自独立 15 KB/窗口预算，任一超限即失败；
  基线快照未刷新——旧基线按同一规则现算分账（分账只改变「哪本账承担」，不改基线数值）；
- 非默认语言语料（en）此前已改 glob 懒加载（`src/plugins/i18n.ts`，产物落独立 chunk
  `virtual_intlify-i18n-*`，不在首屏闭包内），本项只补门禁分账。

### 2026-09-30 验证收尾批（B8 恢复码 + Redis 8.x + k6 基线收紧 + e2e 时长预算）

| 项 | 结果 |
|------|------|
| k6 基线（P1-37 三轮中位数收紧） | routes 682→**1185 rps** / P95 61.8→**28.1ms**（达成 <40ms 验收）；login P95 174→**66ms**（argon2id 单次校验快于 PBKDF2-87 万轮）；metadata P95 163.8→**150.9ms**（未达 <60ms，如实登记；**⚠️ 旧混跑口径已失效**——with_meta 与纯元数据同队列混跑挤高 P95，后被三变体独立成例取代，见下「2026-09-30 读路径优化批 · 元数据口径修正」，勿引用本值）；03-list P95 147.5→**198.9ms**（退化如实登记，成因待查：变化面含 cached_db session / 通知批量化 / 权限缓存 L1 等） |
| 压测环境 | 专用容器 PG 17.11 + **Redis 8.10.2** + 1000 种子用户 + gunicorn×4（生产同参，keep-alive 5） |
| e2e 时长预算 | `darwin-local-full/4`：596s → **792s**（并行全量 4/4 exit=0、490 passed / 0 flaky 后 `E2E_BUDGET_UPDATE=1` 实测刷新）；**坑**：agent 工具壳注入的 `CI=true` 会改预算环境键与重试档位，本地复跑必须 `env -u CI` |
| 前端包体 | 代码 486.8/481.4（+5.4 ✓）/ i18n 65.0/55.9（+9.1 ✓，MFA 恢复码 8 词条 +0.2KB） |
| 后端 | 全量 pytest **4496 passed / 2 skipped**（MFA 恢复码单测 14 + 集成 10；metadata 载荷缓存两处键盲区修补；异步 SDK `_OwnedStream` status_code 修补；codegen 批次 AI triage / 种子 title 欠账修复） |
| 双副本冒烟 | prod+scale overlay：one-off migrate（mfa.0001）、双 server 副本 healthy、nginx multi 两副本 IP 轮询、celery ping 2 节点；暴露的 2 个 prod 形态缺口（SECRET_KEY/ALLOWED_HOSTS overlay 注入、镜像 procps）已收口并登记 deployment.md |


### 2026-09-30 附录 B 收口批（安全/正确性小项 + 动态表单物化筛选列，ADR-075）

| 项 | 结果 |
|------|------|
| 附录 B 后端七项（B1~B7） | 授权写入校验收敛到当前用户可授权面（非超管不可越面配置）；双 header PAT 兜底路径按哈希 60s 缓存（正常请求不触发，异常构造路径不再每请求查库）；审批流程节点数上限 100 + 推进/模拟一次取数（节点查询数不随流程长度增长）+ 环检测显式栈；节点任务 `bulk_create`（多候选一次 INSERT）+ 抄送标识一次批量解析；dform PATCH 局部更新先合并库内数据再整份校验（必填误报修复）；available-flows/available-forms/form-options 三个数据源接口统一 200 条上限与超限提示；节点无候选自动通过补出站 Webhook `flow.node_auto_approved` + 超管知会 |
| 动态表单物化筛选列（ADR-075） | 新增 `filter_data` 物化列 + GIN 索引（迁移 dataset 0005）；五条写入路径统一物化；筛选编译为单条 JSON 包含查询（PostgreSQL 命中 GIN；sqlite 退化逐键精确比较，语义差异登记）；设计器「可筛选」开关 + 「表单数据」页字段筛选行（条件随列表与导出下发）；`rebuild_dform_filter_data` 兼容存量提交 |
| 前端（B17 / B16） | 403 清动态路由快照（会话内被收权自愈；`clearRouteSnapshot` 唯一清入口）+ 快照版本校验失败 30s 退避重试 2 次；**B16 显式取舍**：维持「单测管逻辑、E2E 管界面」分工，UI 层盲区定量沿用 2026-09-27 行与 `xadmin-client/vitest.config.ts` 注释（本批不扩 vitest include） |
| 后端 | 全量 pytest **4524 collected / exit 0**（本批新增 28 例）+ ruff check/format / mypy（691 文件）/ 行数（0 超标）/ 跨 app / 缓存键 / `makemigrations --check` / 文档四件套全绿 |
| 前端 | typecheck / eslint（--max-warnings 0）/ prettier / `check:i18n`（zh 3308 = en 3308）/ vitest **688**（+8：筛选纯函数 6 + 403 清快照 2）全绿；e2e：`dform.e2e.ts` 增设计器「可筛选」开关、`dform-data.e2e.ts` 增筛选命中/清空用例 |
| 坑（已登记） | django-filter 元类只从「自身带 `declared_filters` 的基类」收集声明过滤器——普通 mixin 里声明的过滤器被静默丢弃（筛选参数被忽略、全量返回）；SQLite 不支持 JSONField `contains` 查询，物化筛选按后端分层 |

### 2026-09-30 读路径优化批（纯读免事务 + 在线态快照 + 元数据口径修正）

| 项 | 结果 |
|------|------|
| 纯读请求免 `ATOMIC_REQUESTS` | `packages/xadmin-common/common/core/atomic_read.py`：GET/HEAD 且 action ∈ {list, retrieve, search_fields, search_columns, choices, suggestions} 不套事务（省 BEGIN/COMMIT 两次 DB 往返）；自定义 GET action（导出等带写副作用）与全部写请求保持「整请求一个事务」；config.yml `ATOMIC_REQUESTS_SKIP_READ_ACTIONS=false` 可回退。实测 03-list 单 VU p50 **18.3→16.6ms**；PG 事务计数验证豁免生效（20 请求事务提交数 ~1/请求 → ~2.4/请求）。**实现约束**：ASGI 下 `make_view_atomic` 在事件循环线程执行、同步中间件在线程敏感执行器执行 → 用 contextvar（非 thread-local）绑定当前请求 |
| 在线态 5s 快照 | `message/utils.py::ONLINE_LAYERS_CACHE_KEY`（与在线列表页/聊天在线态同源同 TTL）：用户列表「在线数」、登录日志「在线态」读快照；强制下线/登出走 `use_snapshot=False` 实时口径（不吃 5s 延迟）。30 在线用户下列表请求进程内 A/B **16.68→14.27ms（−14.5%）** |
| 合计（1 VU 服务时间口径） | **20.7 → 16.6ms（−19.8%）**；**未达预估 −30%**——剩余成本以 6 次 DB 往返为主（本机 OrbStack 链路每次约 1.2ms，属环境放大；生产同网络低一个量级）。20 VU 的 P95 本机噪声下无显著差异（排队/CPU 竞争主导），不据此宣称收益 |
| k6 复测（三轮中位数，20 VU/1m） | 03-list rps 224.39→**255.59** / p95 198.94→**196.9ms**；`04-metadata-columns` p95 **30.91ms → 达成 <60ms 目标** / rps 1047；`04-metadata-fields` p95 **106.71ms → 未达 <60ms**（**已归因**：p50 18.6ms 与 P95 逾 100ms 的落差 + 重尾集中于 5 分钟窗口切换点 ⇒ 缓存窗口到期瞬间的并发击穿；修复=载荷缓存单飞锁，见下「第二波」记录，复测待固定环境窗口）；`04-metadata-with-meta`（页面首开）p95 220.85ms / rps 232（列表+内联，与 03-list 同域，不适用该目标） |
| 元数据口径修正（②） | `04-metadata.js` 增 `VARIANT=columns\|fields\|with_meta` 各落独立结果文件、`run-all.sh` 三变体独立跑批；`baseline.json` 以三变体独立成例（移除混跑 aggregate 例）；**<60ms 目标只对纯元数据端点（columns/fields）成立**——混跑时 with_meta 在同一队列把纯元数据的 P95 挤高，口径失真（修正记录见 `docs/ops/performance-baseline.md` §二/§六/§九） |
| 守护测试 | 新增 `tests/unit/common/test_atomic_read.py`（动作矩阵 11 例 + handler 混入行为 + contextvar 复位 + ASGI/WSGI 入口混入断言）；`test_online_stats.py` 增快照用例（命中不二次访问 layer / 过期重建 / 反向索引为空降级 / 实时口径不写快照）并适配原实时口径用例 |

### 2026-09-30 第二波（公式字段 / MCP client / ws-frame 生成 / metadata 单飞 / 依赖准备）

| 项 | 结果 |
|------|------|
| dform 公式计算字段 | 后端 `dataset/utils/dform_formula.py`：显式 tokenizer + 递归下降（**无 eval**）、引用白名单（标量 `{key}` / 表格列 `{table.column}` 仅限聚合语境）、函数 SUM/AVG/MIN/MAX/ROUND/ABS、None 传播、结果统一 round 6 位（half up 与 JS 一致）、SUM/AVG 按行序累加（与前端 bit 级同结果）、嵌套公式按依赖序求值、自引用/循环引用 schema 校验期拒绝；前端镜像 `src/views/form/utils/formula.ts` + `formulaEval.ts`（同口径向量测试）；贯通设计器属性弹窗（公式字段类型 + 表达式编辑 + 环检测拦截保存）/填报联动/详情展示/数据页格式化 |
| MCP client 侧 | `ai/models/mcp.py`（McpServer：名称/URL/鉴权头/超时/工具白名单快照）+ `ai/utils/mcp_client.py`（Streamable HTTP：initialize → notifications/initialized → tools/list → tools/call，JSON 与 SSE 双承载、`Mcp-Session-Id` 回带、SSE 按 bytes utf-8 解码）+ `ai/views/mcp_client.py`（CRUD + sync + call）；安全口径与出站 Webhook 同源（`packages/xadmin-common/common/utils/outbound.py`：https 强制、http 仅 loopback 或白名单、`pinned_request` 固定解析消除 DNS rebinding、响应体上限、白名单外的工具调用 fail-closed）+ 调用审计；7 权限点/菜单（AiMcpServers）+ 前端管理页（列表/档案表单/工具抽屉）；AI 模块裁剪面同步纳入该菜单 |
| ws-frame schema 生成化 | 真源 `message/ws_schema.py`（action 枚举由 `MessageAction` 生成、payload properties 由 `protocol.py` 的 TypedDict 经 `typing.get_type_hints` 导出，required/additionalProperties/描述在真源声明）+ `scripts/gen_ws_frame_schema.py`（生成 / `--check`，纯 Python 无 Django 依赖）+ `docs/schema/ws-frame.schema.json` 由手工双写转生成物；守护 4 例（落盘==渲染 / properties 键集合与 TypedDict 双向对账 / required ⊆ properties / 枚举一致）；client 镜像与 `src/api/types/ws-frame.d.ts` 同步 |
| metadata 载荷缓存单飞 | `packages/xadmin-common/common/core/modelset/metadata_cache.py::cached_payload`（窗口到期并发未命中单飞重建、锁内二次检查复用赢家、`LockError` 降级直建、`builder()` 返回 None 失败不入缓存），`search_fields` / `search_columns` 双端点接入（顺带修正 search_fields 的「失败也缓存」口径）；守护 5 例（赢家复用 / 未命中构建一次 / 锁超时降级 / None 不缓存 / bypass）；重尾归因与复测口径见 `docs/ops/performance-baseline.md` §九 |
| 依赖准备 | `package.json` typecheck 拆 `typecheck:tsc`（tsc --noEmit）与 `typecheck:vue`（vue-tsc）两个子命令并组合（TS 7 切换时只替换前者）；renovate 新增 vue-tsc 规则（`allowedVersions: "<4"` + 主版本不自动合并）；Node 26 实查仍未转 LTS（v26.10.0 `lts:false`，窗口未到，登记待办） |
| 后端验证 | 全量 `pytest` **4631 passed / 2 skipped / exit 0** + ruff check/format / mypy / 行数 / 跨 app / 缓存键 / `makemigrations --check` / 文档四件套全绿；本波新增（收集口径）：公式 47、MCP 单测 19 + 集成 11、ws-frame 4、载荷缓存单飞 5、读路径 11 与在线快照用例等 |
| 前端验证 | typecheck（tsc + vue-tsc）/ eslint / prettier / stylelint / `check:i18n` / 契约三件套 / `as unknown as` 基线 / 行数 / vitest **717 passed（97 文件）** / build + 包体分账全绿 |
| e2e | `dform-formula.e2e.ts` + `mcp-client.e2e.ts` 双浏览器 **4 passed**；并行全量两轮暴露的 3 项失败均定位为**测试等待条件缺陷**（等待条件被历史/残留信号提前满足；DB 取证证明产品行为正确）并修复：`dform-linkage` 回滚（专属文案等待）4 passed、`ai-action` 禁用用户与结果表（`expect.poll` 轮询结果）14 passed；`knowledge-vector` webkit 为既有负载瞬态（隔离复跑 2 passed）；经验回填 `e2e/README.md` |
| 坑（已登记） | ① 共享会话/共享库场景下「存在性等待」（`getByText("操作成功")`、`.el-message` first()）会被历史消息或残留提示提前满足 → 同步点必须以「结果可达」为判据（`expect.poll` 轮询或专属文案过滤）；② locale 断言不能写死译文（本地有 .mo 显中文、CI 无 .mo 显英文）→ 用 gettext 同源取值；③ `.last()` 定位在连跑历史数据下会命中旧元素 → 以「含本 run 唯一文本」过滤后重定位 |

### 2026-10-01 O1 性能复测收口（k6 固定环境三轮中位数 + 回写）

| 项 | 结果 |
|------|------|
| 环境 | §3.1 专用一次性容器（PG 17.11 + Redis 8.10.2，仅绑 127.0.0.1）+ 1000 perf_ 种子 + gunicorn×4（UvicornWorker、keep-alive 5、生产同参，本机 8897 端口避开常驻栈）；`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`（macOS fork 安全，§3.1） |
| 结果（三轮中位数 → baseline.json 全量回写） | 01-login P95 66.18ms（基线 66.03）；02-routes 28.38（28.06）；03-list **192.20**（196.90，连续两轮复现，佐证 §九「排队放大」归因、非代码退化）；04-metadata-columns **28.67**（30.91，<60ms 达标）；04-metadata-fields **117.62**（106.71）；04-metadata-with-meta 218.07（220.85）；05-export 776.91（770.78）；06-import 57.06（57.13）；全部用例 0 错误、`login_throttled` 0，环境复现性 1.0x 量级 |
| **04-metadata-fields 未达标（显式登记）** | P95 117.62ms，未达 <60ms 目标且与基线同平台——单飞锁（2026-09-30）修复的「窗口切换并发击穿」已消除，但 20 VU 尾部由**排队放大 + ASGI 每请求新建 DB 连接开销**主导（§3.1 ⚠️ 根因）。p50 ~18ms 与 P95 落差与首测归因一致；进一步收敛不在元数据端点内继续做，走 ASGI 连接池根因技术债（psycopg3 `OPTIONS.pool` / pgbouncer）。已在 `loadtest/baseline.json` 该用例加 `target/target_met/target_note` 显式登记 |
| 结论 | O1 收口：唯一量化缺口（fields <60ms）判定为「受 ASGI 连接模型主导、端点内无单点热点」，不再作为元数据端点优化项挂账；基线 JSON 八用例全部回写为 2026-10-01 固定环境三轮中位数 |

### 2026-10-01 fields P95 深度定位（对照实验修正当日早间归因）

| 项 | 结果 |
|------|------|
| 动机 | 「排队放大 + 连接开销」解释不了 fields（P95 117ms）与 columns（29ms、更高吞吐）的 4 倍差距——同框架同负载，分位数据（fields p50 18ms / p90 43ms / p95 117ms 双峰）指向 fields 专属尾部事件 |
| 对照实验（临时探针中间件 + 逐请求时间线 + 缓存 TTL 采样 + worker 内栈采样，环境用后即毁） | ① 温热缓存（零重建窗口）下慢桶依旧 5.8% → 排除重建/单飞锁；② 缓存键稳定（TTL 300s 单键），重建整轮仅 1 次 ≈0.25%，量级不符 → 排除窗口击穿残留；③ 单连接 300 发 0 个 >100ms（p95 6.4ms）→ 命中路径代码干净，无逐请求浪费；④ 慢请求 89% 成簇（簇 8-20 ≈ 全部 VU）、TTFB≈总时长 → 服务端停顿波；⑤ book 同框架对照 2000 rps 下 P95 13.7ms 干净 → 排除鉴权/框架通用层 |
| **并发扫描（定案）** | fields：4 并发 = **725 rps / P95 7.8ms / 慢桶 0.02%**；8 并发 P95 83ms；20 并发 = 820 rps / P95 105-130ms；40 并发 = 778 rps / P95 169ms——吞吐在 ~780 rps 封顶、加并发只加延迟 = **串行容量膝点**（ASGI 同步执行段，栈采样定位在 sync→async 交接等待，占请求线程时间 21.6%）。columns 膝点 ~1100 rps（40 并发才现桶）、book ~2000+，解释了此前 endpoint 差异 |
| **容量验证** | 8 worker（其余同参）+ 20 VU：fields **P95 26.5ms（<60ms 达标）**、吞吐 1209 rps、慢桶 1.73% |
| 登记修正 | `loadtest/baseline.json` fields 条目 target_note 改写：未达标根因 = ASGI 同步段串行容量（4 worker ≈780 rps），非元数据端点代码问题；达标杠杆 = 容量（worker 数）或同步段优化（异步中间件链迁移），均另立容量规划，不在端点内继续优化 |

### 2026-10-01 晚 8-worker 容量档位复测（容量立项批 A1，承接上节「另立容量规划」）

| 项 | 结果 |
|------|------|
| 环境 | §3.1 同款固定环境（专用 PG 17.11 + Redis 8.10.2 + 1000 perf_ 种子），gunicorn UvicornWorker **×8**（其余同参，本机 8897）；Redis 发布端口改 56380（56379 被测试套件真库容器占用，仅端口差异）；压测轮次落 `loadtest/k6/results/cap8-round1..3` |
| 结果（三轮中位数） | 01-login P95 67.31ms（4-worker 基线 66.18，持平）/ 02-routes 17.80（28.38）/ 03-list **105.12**（192.20）/ 04-metadata-columns 22.91（28.67）/ **04-metadata-fields 25.74（<60ms 达标**，基线 117.62，0.22x）/ with_meta 192.51（218.07）/ 05-export 476.93（776.91）/ 06-import 45.49（57.06）；全部用例 0 错误（06-import 的 round1 因 1 个请求失败率非 0 按 §六 作废，取 2/3 轮中位数） |
| 登记口径（决策点 D1） | 快照主体（`env.server`）**维持 4-worker 生产默认形态不变**（护栏对默认形态负责，fields `target_met=false` 保留作容量参照）；8-worker 三轮中位数登记到 `baseline.json` 顶层 `capacity_reference`（不参与 `check_baseline` 回归判定），fields `target_note` 追加 8-worker 达标档位 |
| 复验 | `check_baseline.py --results cap8-median`（不 `--update`）**0 项劣化** PASS；fields rps 777.1→1101.5（1.42x） |
| 批 A2 同步 | `docs/ops/deployment.md` 新增 §3.3「Web 层容量规划」（worker 数杠杆实测表 / 容量指引 / DB 连接联动核算）+ §9.4 速查表行更新 + `config_example.yml` `GUNICORN_MAX_WORKER` 注释；完整台账见 [ASGI 同步段容量立项](plans/ASGI同步段容量立项-2026.10.md) §八 |

### API 尾延迟目标正式定案（2026-10-09）

| 项 | 结论 |
|------|------|
| 定案（下调） | `04-metadata-fields` 的「P95 < 60ms」目标口径修正为**按生产容量档判定**：8-worker 档位 P95 26.5ms 达标（`baseline.json` 顶层 `capacity_reference` 三轮中位数 25.74ms）；4-worker 默认档的 P95 117.62ms 判定为 **ASGI 同步执行段容量膝点**（非缓存重建 / SQL / 逐请求代码浪费），达标杠杆是容量（worker 档位）而非元数据端点代码。`baseline.json` 该用例 `target` 已改写为容量口径、`target_met=true`，`target_note` 保留 117.62ms 实测与 8-worker 26.5ms 容量参照。 |
| 同域用例口径统一 | `03-list`（默认档 192.2ms / 容量档 105.12ms）与 `04-metadata-with-meta`（默认档 218.07ms / 容量档 192.51ms）与 `04-metadata-fields` 统一为同一容量口径（三者均无独立 P95 目标，仅登记容量参照）。 |
| 依据 | ①对照实验（4/8/20/40 并发扫描 + 双端点对照 + worker 内栈采样）定位为 ASGI 同步段串行容量膝点（fields 约 780 rps 封顶），4 并发即 P95 7.8ms；②容量验证：8 worker 下 P95 26.5ms（<60ms 达标）、吞吐 1209 rps；③同步段优化（异步中间件链迁移）已交付，为容量之外的第三条杠杆。 |
| 残留观察项 | 4-worker 默认档的 P95 回归**仍由 `check_baseline.py` 的 1.2x P95 容差门禁保护**（`loadtest/baseline.json` 的 `tolerance.p95_ratio`）——口径下调只针对「固定目标判定」，不放松回归护栏：默认档若继续劣化仍会被门禁拦下。 |
