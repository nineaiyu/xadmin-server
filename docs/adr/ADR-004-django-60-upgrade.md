# ADR-004: Django 升级决策——停留 5.2 LTS，等待 6.2 LTS 走 LTS-to-LTS 跳档

- 状态：已接受（2026-09-06，P5 前置评估）；**修订（2026-09-11）：6.2 升级取消**，见文末复审记录
- 关联：半年规划 T5.2 / 风险 R3（技术栈追新）；遗留缺口 L4
- 事实校准：规划文档原表述「2026-12 发布的 Django 6.0 LTS 候选」有误——Django 6.0 已于 2025-12-03 发布且**不是 LTS**（x.0 均非
  LTS），下一个 LTS 是 2026-12 计划发布的 **Django 6.2**（x.2 系列均为 LTS，如 4.2/5.2）。

## 背景

当前后端为 Django 5.2.9（LTS，官方支持至 2028-04），运行于 Python 3.13。Django 6.0 已发布（2025-12），要求 Python ≥
3.12，主要新特性：原生后台任务框架（django.tasks）、内置 CSP 支持、模板 partials、部分索引增强。

规划 T5.2 要求评估是否升级，默认不升，除非明确受益。

## 备选方案

1. **升 6.0/6.1（当前已发布的非 LTS 版本）**；
2. **停留 5.2 LTS，2027-01 升级窗口直接评估 6.2 LTS（LTS-to-LTS）**；
3. 停留 5.2 不再升级。

## 决策

**方案 2**，理由：

1. **无安全压力**：5.2 LTS 官方支持至 2028-04，覆盖本规划期（2027-02 结束）与下一个半年规划期，不存在被迫升级的时间点；
2. **避免非 LTS 中间跳板**：升 6.0/6.1 意味着在 6.2 LTS 发布后又要做一次强制升级（非 LTS
   只维护到下一版本发布），两次升级成本 > 一次 LTS-to-LTS；
3. **依赖兼容矩阵待验证**：channels 4.3.2 / channels-redis / celery 5.6 + django-celery-beat/results / DRF 3.16.1 /
   simplejwt 5.5.1 / django-redis / django-filter / drf-spectacular 等对 6.x 的兼容声明需在 6.2 发布后逐一核实，现在动手只能对着
   6.0 验证一遍、6.2 再验证一遍；
4. **新特性吸引力有限**：后台任务框架与现有 Celery 栈重叠（且 Celery 承载 beat 定时与队列分离，不会迁移）；CSP 内置支持是真实增益，可作为
   6.2 升级后的安全加固项，不构成提前升级的理由；
5. **符合规划风险管理第 3 条**：升级一律独立分支 + 全量门禁，默认不升。

## 实施项（归入 2027-01 T5.1/T5.2 升级窗口）

- 2026-12 6.2 LTS 发布后：核对官方发布说明中 5.2 → 6.2 的 deprecation 清单，跑 `django-upgrade` 预处理 + `python -Wd`
  全量测试收集弃用告警；
- 逐个核实兼容矩阵并锁定版本：DRF、channels/channels-redis/daphne、celery
  全家桶、simplejwt、django-redis、django-filter、django-cors-headers、drf-spectacular、django-imagekit；
- 独立分支 `feat/django-62`，全量门禁（pytest 579+ 用例、覆盖率 ≥75%、E2E）通过后合入；
- 升级后安全加固候选：启用内置 CSP 支持替代/补充现有响应头策略；
- 若 6.2 兼容矩阵不成熟（核心三方库未声明支持），顺延一个季度并在本文档追加复审记录。

## 后果

- 正面：避免双跳升级；升级窗口落在 LTS 上，维护周期与规划期对齐；CSP 获得官方内置支持路径；
- 负面：2027-01 前无法使用 6.0 新特性（当前无需求方）；CSP 需等到升级后才能启用；
- 中性：Python 3.13 已满足 6.x 最低要求，运行时环境无需变动。

## 复审记录（2026-09-11，L4 关闭）

**决策修订：取消 Django 6.2 LTS 升级（无限期），停留 5.2 LTS。**

理由：celery 全家桶（celery 5.6 + django-celery-beat + django-celery-results）
尚不声明支持 Django 6.x，而 Celery 承载 beat 定时任务与 default/heavy 队列分离，
是不可替换的核心依赖（ADR-004 第 4 条已确认不会迁移到 django.tasks）。核心
依赖矩阵不满足时强行升级违反「升级一律全量兼容验证」纪律，本项关闭。

- 触发重开条件：celery 主线发布并声明支持 Django 6.2 LTS（含 beat/results/
  beat 调度器全链路），且存在必须升级的安全或特性驱动；
- 影响：遗留缺口 L4 关闭；「升级后启用内置 CSP」候选项一并搁置（现有
  X-Frame-Options 等响应头策略不变）；5.2 LTS 官方支持至 2028-04，无安全压力。

## 复审记录（2026-09-11 第二轮，触发条件核查 → 仍未满足）

按排期 D2「Django 6.2 重开条件核查（是否升级到 6.2 LTS）」复核 PyPI 现状：

| 依赖 | 最新版本 | Django 支持声明 |
|---|---|---|
| Django | 6.1.1 | —（**6.2 尚未发布**，x.2 LTS 计划 2026-12） |
| celery | 5.6.3 | 无 Django framework classifiers（未声明 6.x） |
| django-celery-beat | 2.9.0 | 声明至 Django 6.0 |
| django-celery-results | 2.6.0 | 声明至 Django 5.2 |

结论：**重开条件未满足，维持「不升级」**（本仓当前运行 Django 6.0.8 + Python 3.13，
beat/results 的声明矩阵亦未覆盖 6.0 之上版本）。下次复核时机：Django 6.2 正式发布
（2026-12）且 celery 主线（含 beat/results）声明支持 6.2 后，再按 D2 口径复核一次。

配套说明：原「升级后启用内置 CSP」的诉求已由独立方案解除绑定——S3 采用
`django-csp 4.0` + 运行期模式开关（`CSP_MODE`：disabled/report-only/enforce）落地，
默认 report-only 观察，观察期结束后切 enforce，无需等待框架升级（见
`docs/ops/deployment.md` 与 `docs/security-review.md`）。

## 复审记录（2026-09-15，下一年度规划 W3–W4 依赖窗口评估）

- 本仓当前运行 **Django 5.2 LTS**（支持至 2028-04）；6.2 LTS 计划 2026-12 发布，
  **尚未发布**，重开条件（6.2 发布且 celery 全家桶声明支持）未满足；
- 结论：**维持不升级**，下次复核时机不变（2026-12 6.2 发布后按 D2 口径复核一次）；
  在 2028-04（5.2 LTS 支持结束）前完成迁移评估即可，无近期动作。

## 复审记录（2026-09-16，依赖窗口实测：升级尝试 → 声明阻断 → 回滚）

按年度依赖窗口实测复核（含升级尝试，全程留档）：

| 依赖 | 最新版本 | Django 支持声明 | 结论 |
|---|---|---|---|
| Django | **6.1.1**（6.2 LTS 仍未发布） | — | 见下 |
| celery | 5.6.3 | 无 6.x classifiers | 维持 |
| django-celery-beat | 2.9.0 | **`Django<6.1`**（pip 依赖检查实报不兼容） | **阻断 6.1** |
| django-celery-results | 2.6.0 | 声明至 5.2（滞后，运行于 6.0 未报错） | 维持 |

**升级尝试记录**（6.0.8 → 6.1.1，本机 venv + server/worker/heavy 三容器）：
Django 6.1.1 下全量测试 **2358 passed / 1 skipped 零回归**（运行时实际兼容），
但安装期依赖检查实报 **`django-celery-beat 2.9.0 requires Django<6.1`**——
按「声明矩阵未覆盖即维持」纪律**全量回滚至 6.0.8**（venv + 三容器；回滚后 `pip check`
无冲突、线上健康检查 db/redis/celery 全绿）。

结论：**维持 Django 6.0.8**。本轮把「升级待评估」推进为「**升级目标 6.1 已被 beat 声明阻断**
（并非仅等待 6.2）」——重开条件细化为：**django-celery-beat 发布支持 ≥6.1 的版本**（results 同步跟进），
或 6.2 LTS 发布且全家桶声明覆盖后，按本轮同口径（升级 → 全量门禁 → 回滚预案）执行。

**口径修正**：本仓实际运行 **Django 6.0.8 + Python 3.13.15（venv）/ 容器 Python 3.14.7**；
上一节记录「当前运行 5.2 LTS」与实测不符，以本节实测为准。

配套：依赖审计 pip-audit / pnpm audit 双 0 漏洞；Python 线容器基线 `python:3.14.7-slim` 已提线，
本机 venv 3.13.15 差异登记（工具链对齐列入下一年度评估）。

## 复审记录（2026-09-29，第二轮规划 P0-D1：EOL 停留决策与安全监控登记）

**触发**：第二轮重构规划（`REFACTORING-PLAN.md` §9.1 D1）把「Django 6.0.8 已退出安全支持」列为 P0 最高优先级，
要求按本 ADR 既定流程复核升级可行性。

**核验（当日实查 PyPI 元数据 + 本地安装声明，阻断面首次收敛到单包）**：

| 依赖 | 声明 | 对 6.1 的影响 |
|---|---|---|
| **django-celery-beat 2.9.0** | `Django<6.1,>=2.2` | **阻断**（PyPI 最新仍为 2026-02-28 发布的 2.9.0，无新版时间表） |
| django-timezone-field 7.2.2 | `>=4.2,<6.2` | 放行 6.1；**卡未来的 6.2 LTS** |
| django-redis 7.0.0（`<7.0`）/ channels 4.3.2 / DRF 3.18.1 / django-filter 26.1 / django-csp 4.0 / simplejwt 5.5.1 / drf-spectacular 0.30.0 / django-celery-results 2.6.0 | 仅下界 | 放行 |

对比 2026-09-16 记录（当时 beat 声明阻断、其余未逐一核对）：本轮确认**除了 beat 之外没有任何包声明卡 6.1**，
「升级待评估」可以精确表述为「**等 django-celery-beat 一个包放宽声明**」。

**决策（用户确认）**：**维持 Django 6.0.8**，本轮不启动 override 升级。理由：
① 阻断来自不可替换核心依赖（beat 承载定时任务调度）的显式声明，override 等于推翻本 ADR 的「声明矩阵未覆盖即维持」纪律，
须由安全事件驱动而非版本号焦虑驱动；② 6.0 线虽无新补丁，但本部署面为 Django + DRF + channels/daphne + celery 栈，
无第三方 Django 插件面，历史公告命中面有限；③ 已具备监控 + 双路径处置能力（下节）。

**配套动作（已交付）**：

1. **EOL 期安全监控进清单**：`docs/ops/release-checklist.md` §0 新增「Django 线安全公告核对」基线项
   （每次发布窗口 + 每季度依赖窗口执行），执行记录逐窗口追加；
2. **处置双路径登记**（`docs/security-review.md` 六期登记 S-1）：命中 6.0 线公告时按影响面选择
   「紧急升级 6.1（override：`[tool.uv] override-dependencies` 注释原因与撤销条件 + 全量门禁 + beat 周期任务链路专项验证 + 回滚预案）」
   或「上游补丁后移到自有镜像（记录补丁来源与到期时间）」；
3. **接受项入台账**：`docs/security-review.md` S-6 接受项台账新增「Django 6.0.8 停留」行（接受理由 + 重开条件）；
4. **Python 口径对齐（D3，同批交付）**：`requires-python`/mypy `python_version` 与 9 处 CI workflow 由 3.13 对齐到 **3.14**
   （容器基线 `python:3.14.7-slim` 与本机 venv 早已 3.14.7），`uv.lock` 重生成，server 内 6 处文档与文档站 4 文件
   5 处受保护事实同步——本条关闭 2026-09-16 记录的「本机 venv 版本差异登记」。

**重开条件（细化）**：`django-celery-beat` 发布声明支持 `Django>=6.1`（beat/results/timezone-field 三件同步覆盖），
或 6.2 LTS 正式发布且全家桶声明覆盖；此外**新增安全触发**：出现影响本部署形态的 6.0 线高危公告时，
立即按上述「紧急升级」路径执行（不再等待季度窗口）。届时仍按既定纪律：独立分支 + 兼容矩阵 + 全量门禁 + 回滚预案。

