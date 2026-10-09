# ADR-076：AI 计费与成本核算（功能立项）

- 日期：2026-09-30
- 状态：**暂不实施（触发制登记）**——当前部署形态下无成本结算受众，不占开发窗口；实施预研已固化（形态见下），触发条件满足再按 D6 立项实施
- 关联：`REFACTORING-PLAN.md` 附录 B 的 AI 计费条目（B14）；ADR-031（多租户 no-go——同一商业化逻辑）、ADR-074（触发制登记先例）；ADR-023（AI 接入层）、ADR-047/ADR-048（AI 工具层）；`ai/models/ai.py`、`ai/utils/ai_usage.py`、`ai/views/assistant.py`、`xadmin-client/src/views/integration/ai/config.vue`
- 背景：AI 链路已具备完整「量」的观测与治理（用量账本、三级配额、调用观测），但没有「钱」的口径。本项登记自对标缺口（同类项目把 AI 计费做成多租户 SaaS 的商业化组件），走查后评估：与 xadmin 单组织定位不匹配，暂不实施，固化预研待触发。

## 现状（事实基础）

1. **用量账本**：`AiUsageRecord`（`ai/models/ai.py:180-224`）逐次记录 `feature`（docs/chat/nl/action/embedding）、`track`、`profile_name`、`model`、`tokens_in/out/total`、`duration_ms`、`ok`、`detail`；按 (creator, -created_time) 与 (feature, -created_time) 建索引；保留期随 `MONITOR_RETENTION_DAYS` 由周期任务清理（`ai/utils/ai_usage.py:441-463`）。
2. **写入口已收敛**：`record_usage`（`ai/utils/ai_usage.py:78-111`）是唯一记账点，由 `tracked_chat` / `tracked_chat_tools` / `tracked_chat_stream` 三个包装覆盖同步、工具、流式（含异步 SDK 链路）全部消费方；记账失败只记日志不影响业务。
3. **汇总端点已具备**：`usage_summary`（`ai/utils/ai_usage.py`）提供按天 / 链路 / 双轨 / 档案 / 模型 / Top 用户聚合（合计与档案、模型维度含成功率与延迟，均值恒给、P95 样本足够才给）+ 配额 + 并发槽；出口 `GET /api/ai/assistant/usage`（`ai/views/observability.py`），权限沿用 `(status|metrics|history|tools|usage)$` 路径正则。
4. **配额三级已具备**：日调用次数 / 日 token / 并发流式（`quota_limits` / `quota_error`，`ai/utils/ai_usage.py:61-66,165-181`），超限可读拒绝。
5. **档案体系**：`AiProfile`（`ai/models/ai.py:101-177`）含 `model` 与用途分流（chat / structured / embedding），无任何价格字段。
6. **前端观测面**：AI 配置页用量面板（`xadmin-client/src/views/integration/ai/config.vue:128-386`）——四张卡片（调用 / token / 失败 / 并发槽）+ by_feature / by_track / top_users 标签云；类型 `AiUsageSummary`（`src/api/ai/ai.ts:189`），接口方法 `aiAssistantApi.usage`（`:274-279`）。
7. **缺口**：无单价、无成本、无金额维度汇总（「模型」维度已具备，见第 3 条）。

## 结论与触发条件

**结论：暂不实施。** 本项是对标缺口登记（同类项目把 AI 计费做成多租户 SaaS 的商业化组件），与 xadmin 的定位不匹配：

1. **无结算对象**：单组织 / 单实例 / 可自托管形态下没有「卖 AI 服务」的付费方；ADR-031 已否决多租户 SaaS 化——余额 / 套餐 / 账单是同一类商业化组件，结论应一致；
2. **支出规模在供应商侧可查**：本地模型（LM Studio / vLLM 等）成本近似为 0；少量云 API 的月支出在供应商控制台可见；
3. **「量」的治理已在位**：日调用 / 日 token / 并发流式三级配额已覆盖滥用防护；成本口径是可观测性的锦上添花，不是刚需。

**触发条件（任一满足即按下方预研立项实施）**：

| # | 条件 |
|---|---|
| 1 | 出现按部门 / 用户结算 AI 成本的真实诉求（内部分摊、预算控制）； |
| 2 | 云 API 支出达到需要限额治理的量级（持续月度支出、需对部门 / 用户设金额上限）； |
| 3 | 框架分发场景中二开用户提出成本观测需求，或对外提供 AI 服务能力。 |

## 若实施的形态（预研）

触发条件满足后按以下形态实施（本次仅固化预研，不占开发窗口）：

**做**：以「模型定价表 → 记录级成本快照 → 成本汇总与展示」三层落地；成本口径 = 用量 × 单价（估算），服务内部成本可见与分摊。

**仍不做**：余额 / 充值 / 账单 / 发票（无付费对象，属多租户商业化形态，ADR-031 重开条件满足前不立项）；阻断式「余额不足拒绝调用」（配额体系已提供阻断能力，成本是观测面）。

### D1 定价按「模型」维度（新模型 AiModelPrice）

- 新模型 `AiModelPrice`：`model`（匹配键，唯一）、`price_in` / `price_out`（每百万 token 单价，`DecimalField(12,6)`）、`is_active`、`remark`；`DbAuditModel`（改价留审计）。
- 匹配：记账户按 `model__iexact`（大小写不敏感精确匹配）在启用行中查价；定价表规模极小，经 `ai/utils/ai_pricing.py::price_for_model` 提供 60s 缓存（保存 / 删除信号失效，口径与既有配置缓存一致）。
- 为什么按模型而不是按档案：价格是模型的属性——同模型多档案一致计价、档案改名 / 换模型零影响、与供应商公布价直接对齐；档案级定价会把价格与档案生命周期耦合，且同模型多档案需重复配价。
- 「未定价」（无启用行命中）与「零价」（本地免费模型显式配 0）是两种不同语义，见 D2。

### D2 记录级成本快照（AiUsageRecord 扩展）

- 新增列：`price_in` / `price_out`（发生时单价快照，`Decimal(12,6)`，未命中为空）、`cost`（`Decimal(18,6)`，计算值）。
- 计算：`cost = tokens_in/1e6 × price_in + tokens_out/1e6 × price_out`；Decimal 运算、6 位小数、ROUND_HALF_UP。
- **三分语义**：命中定价行 → `cost` 为数值（0 即免费模型）；未命中 → `cost` 为 NULL（报表层单独计「未定价调用数」，**不进合计**，避免把未知成本当 0）；定价行停用 → 按未命中处理。
- 为什么快照而不是报表期 join：改价不重算历史（会计口径正确）；汇总直接 `SUM(cost)`，免关联与缓存失效面。
- 存量行（变更前）：`cost` 为空，不自动回溯（token 有、价不可知）；提供可选命令 `rebuild_ai_usage_cost --days N --model X`（按当前价回填、输出明确标注为估算），登记为运维可选项。

### D3 币种与精度

- 全局单一币种：SysConfig `AI_COST_CURRENCY`（默认 `CNY`，进 `config.py` property 与 `systemconfig.json` 种子）；记录与定价表不存币种，切换币种不换算历史（登记边界，多币种与汇率不做）。
- 单价 6 位小数覆盖「x 元 / 百万 token」量级的全部展示需要（含极小单价）。

### D4 汇总口径扩展（usage_summary）

- 扩展返回：`total_cost`、`unpriced_calls`、`unpriced_models`（Top N 未定价模型名与调用数，前端据此给「去配置定价」引导）；`by_day` / `by_feature` / `by_track` / `top_users` 各行新增 `cost`；新增 `by_model`（calls / tokens / cost / unpriced）。
- 权限不新增：成本随 usage 端点下发（现有 `status:AiAssistant` 路径正则覆盖），定位是管理员观测面；若要向非管理员收窄再拆独立权限点（登记）。

### D5 管理面与前端

- 端点 `api/ai/model-prices`（CRUD ViewSet：list / create / retrieve / partialUpdate / destroy）+ filter（`model` icontains、`is_active`）+ `table_fields`；权限点 5 个（`list/create/retrieve/partialUpdate/destroy:AiModelPrice`）由 `sync_menu_permissions` 生成并挂 AI 配置菜单，随 `load_init_json` 重灌。
- 前端：AI 配置页新增「模型定价」卡片（表格 + 弹窗，遵循既有弹窗收敛模式）；用量面板新增「成本」卡片与各分组成本展示、未定价提示；API 与类型进 `src/api/ai/ai.ts`；词条 zh/en 对称。

### D6 分期与不纳入范围

| 阶段 | 范围 | 触发 |
|---|---|---|
| 阶段一（首发范围） | D1–D5：定价表 + 成本快照 + 汇总扩展 + 定价管理 + 用量面板成本展示 | 触发条件满足即实施 |
| 阶段二（按需） | 按成本的日配额（`AI_QUOTA_USER_DAILY_COST`，与既有三级配额同口径）；成本报表导出（下载中心）；未定价巡检告警面；定价模式匹配（前缀通配） | 出现配额治理 / 对账诉求 |
| 阶段三（触发制） | 部门 / 用户月度预算与超支告警；成本分摊视图 | 出现成本治理诉求（结算 / 分摊） |
| 不纳入 | 余额 / 充值 / 账单 / 发票；阻断式余额计费；多币种与汇率 | 多租户商业化重开（ADR-031 条件） |

## 备选与不选

| 方案 | 不选原因 |
|---|---|
| 余额 / 充值 / 账单体系（商业化形态） | 单组织部署无付费对象；ADR-031 多租户 no-go 的重开条件未满足 |
| 按档案（AiProfile）定价 | 价格与档案生命周期耦合（改名 / 换模型断历史匹配）；同模型多档案重复配价 |
| 报表期实时 join 定价表算成本 | 改价导致历史成本漂移（会计口径错误）；汇总需 join + 缓存失效面 |
| 记录里存原始 usage JSON | tokens 三列已落库，重复存储无增益 |
| 成本按「每 1K token」计价 | 主流定价页是每百万 token，换算与展示多一层 |

## 验收（实施时口径）

- 单测：定价匹配（大小写归一 / 未命中 / 停用 / 缓存命中与失效）、成本计算（Decimal 精度 / 零价 / 单侧价 / NULL 语义）、快照写入（记录含价格快照与 cost）、汇总扩展（total_cost / unpriced 计数与清单 / by_model）；
- 集成：定价 CRUD 权限（无权限拒绝）、usage 端点返回成本字段、一次真实链路调用后成本落库；
- e2e：定价页 CRUD（新增 / 编辑 / 停用）+ 用量面板成本卡片与未定价提示（双浏览器）；
- 门禁：按项目惯例八件套（pytest / ruff / mypy / 行数 / 跨 app / 缓存键 / 迁移检查 / 文档四件套）+ 前端 typecheck / lint / vitest / 契约 / i18n / 包体；
- 部署：migrate（ai 新迁移：定价表 + 用量三列）、`load_init_json`（+5 权限点）、`compilemessages`（词条）。

## 边界（登记）

- 成本是「用量 × 单价」的估算口径，不等于供应商账单（缓存命中折扣、批处理价、阶梯价不建模）；
- 定价按 model 精确匹配，模型别名 / 版本后缀（`-latest` 等）需分别配价（模式匹配留阶段二）；
- 存量记录不回溯（`cost` 为空不计入历史合计，可选命令回填标注估算）；
- 币种全局单一切换不换算历史；
- 成本可见性与用量同权限（沿用现有路径正则），不收窄不拆分。
