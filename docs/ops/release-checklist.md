# 发布窗口 Checklist（运维尾巴收口）

> 建立：2026-09-14（下一年度规划 §三.4「运维尾巴进发布窗口 checklist」）。
> 用途：把依赖外部窗口的运维尾巴固化为每次发布前逐项核对的清单，并承载硬门禁：
> **CSP enforce（§1）与 AES v1 解密关闭（§2）未闭环前，不启动下一年度第一个功能窗口（W1）**。
> 相关：备份演练口径见 [backup-drill-2027-03.md](backup-drill-2027-03.md)；故障处置见 [runbook.md](runbook.md)；
> 安全背景见 [ADR-011](../adr/ADR-011-aes-protocol-v2.md) 与 `server/settings/base.py` 的 CSP 配置。

## 0. 每次发布都过（基线项）

- [ ] **全量门禁**：server `ruff check` + `ruff format --check` + `pytest -n auto`（覆盖率 ≥78%）；
      client `typecheck` + `typecheck:strict` + `eslint --max-warnings 0` + `prettier` + `stylelint` + `vitest` +
      `check:contract` + **`check:bundle-size`**（首屏闭包增长 ≤15 KB，超预算需在 PR 说明后刷新基线）
- [ ] **E2E**：改后端必跑 `pnpm test:e2e:fresh`（防旧进程假失败）；新功能主链路双浏览器 + a11y/smoke 门禁
- [ ] **数据/部署**：新权限点重灌 `python manage.py load_init_json`（幂等验证）→ 重启 `server`/`celery-worker`/`celery-heavy`
      （代码挂载不热加载）；迁移在全新库重建验证
- [ ] **依赖窗口**（12/03/06/09）：client `pnpm audit --registry=https://registry.npmjs.org`、
      server `pip-audit` 零高危；Renovate 挂起项见 §3
- [ ] **备份**：发布前执行备份（异地副本 + 媒体目录，RPO 6h 口径见演练记录）；确认失败告警可达
- [ ] **发布后 30 分钟观察**：`GET /api/common/api/health`、`data/logs/server.log` 错误率、celery 队列无积压、
      `data/logs/unexpected_exception.log` 无新增

## 1.（硬门禁）CSP enforce 切换

**背景**：`SysConfig.CSP_MODE` 默认 `report-only`（`server/conf.py:322`，取值 `disabled / report-only / enforce`），
`CSPModeMiddleware` 按配置改写 django-csp 生成的响应头（`common/core/middleware.py`）；违规上报落 `/api/csp-report`
（`common/api/csp.py`，按「指令+文档路径」60s 节流打 WARNING）。

**前置（全部满足才可切换）**：

- [ ] `CSP_REPORT_URI` 已指向 `/api/csp-report`（空 = 不下发 report-uri，观察期无数据）
- [ ] 生产日志 `data/logs/server.log` 中 `CSP violation:` 命中**连续 7 天清零**
      （统计：`grep -c "CSP violation:" data/logs/server.log`；按 directive 看：`grep -o "directive=[^ ]*" ... | sort | uniq -c`）
- [ ] 若清零前有个别命中，已在 `server/settings/base.py` 策略中豁免并复测（不要用 enforce 直接压掉真实违规）

**动作**：

1. [ ] 置 `CSP_MODE=enforce`（系统配置持久化键 `CSP_MODE`；改后确认生效——`SysConfig` 读缓存，建议经配置更新接口或重启进程）
2. [ ] 浏览器实测核心页：登录、列表页（RePlusPage）、富文本编辑（wangeditor）、图表（echarts）、文件预览
3. [ ] 保留回滚开关：`CSP_MODE=report-only` 即刻恢复观察态（无需发版）

**验收**：响应头出现强制 `Content-Security-Policy`（且不再有 `Content-Security-Policy-Report-Only`）；核心页控制台无 CSP 拦截。

## 2.（硬门禁）AES v1 解密关闭

**背景**：`SECURITY_AES_V1_DECRYPT_ENABLED` 默认 `True`（`server/conf.py:204`）；关闭后旧 `Salted__` 格式密文
一律按非法输入返回空串（`common/base/utils.py` 的 `AESCipherV2.decrypt`）。前端 `aes.ts` 默认走 v2。

**前置（核对前端版本分布）**：

- [ ] 观察窗口内 `data/logs/server.log` 的 **`aes_v1_decrypt_used:`** 标记清零
      （2026-09-14 新增的退役观测点：仅命中**合法旧格式密文**时告警，任意非法输入不触发，可安全用于清零判定）
      - 统计：`grep -c "aes_v1_decrypt_used" data/logs/server.log`
      - 覆盖要求：至少 1 个完整发布窗口（≥7 天）且包含一次全员活跃时段
- [ ] 若仍有命中：结合 `dist/version.json` 与前端发布记录定位未升级客户端（浏览器缓存/长期未刷新页面），
      提示强刷后观察至清零

**动作**：

1. [ ] 置 `SECURITY_AES_V1_DECRYPT_ENABLED=false`（`config.yml` 运行期配置），重启 server
2. [ ] 回归：登录（密码走 AES 传输）、修改密码、系统配置密钥类字段读写、IM/OAuth 凭证类配置
3. [ ] 保留 1 个发布窗口的回滚准备（改回 `true` 即恢复兼容）

**验收**：观察窗口内无 `aes_v1_decrypt_used`；上述回归路径全部通过。

## 3. 挂起项（依赖外部窗口，逐窗口检查）

| 项 | 内容 | 触发/解除条件 |
|----|------|---------------|
| 生产异地副本独立故障域核对 | 备份副本与生产不在同一故障域（机房/账号/存储），核对副本可独立恢复 | 每季度备份演练时一并核对（记录追加到 ops/backup-drill-*.md） |
| Renovate main 合入 + dispatch 验收 | `renovate.yml`/`renovate.json` 仅在 dev 分支，非默认分支 dispatch 返回 404、cron 不生效 | 下次例行合入 main 后执行 `gh workflow run renovate.yml` 补跑验收（并确认 `RENOVATE_TOKEN`） |
| SCIM 真实 IdP 联调 | Okta/Entra 真实目录接入（需租户资源） | 有真实 IdP 资源时插入执行；参考 [scim.md](../architecture/scim.md) |
| PITR（WAL 归档） | RPO 6h → 分钟级 | 仅当 RPO 升为硬需求并经成本评审（候选池，见 plans/README.md） |

## 4. 执行记录（逐窗口追加）

| 窗口 | 日期 | 项 1 CSP enforce | 项 2 AES v1 关闭 | 备注 |
|------|------|------------------|------------------|------|
| W0（下一年度收口） | 2026-09-14 | 未闭环（待生产 report 7 天清零；开关与回滚已就位） | 未闭环（待观察窗口内 `aes_v1_decrypt_used` 清零；观测点已交付） | 硬门禁保持：不启动 W1 功能窗口 |
