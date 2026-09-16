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
      - **2026-09-16 起该判据已可判**：`common/api/csp.py` 增加合成上报隔离，非真实浏览器来源
        （缺 `document-uri` / 脚本 UA / 非本站文档域）只记 `CSP synthetic report ignored:`（INFO），
        不再写入 `CSP violation:`。隔离前当日 69 条命中**全部**为合成上报，判据无意义。
      - 排查合成来源仍可查原始字段：`grep "CSP synthetic report ignored" data/logs/server.log`（含 reason/directive/blocked/document）
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
- [ ] 若仍有命中：按日志里的 **`caller=`**（2026-09-16 新增，形如 `login.py:313 do_login`）定位来源，
      再结合 `dist/version.json` 与前端发布记录定位未升级客户端（浏览器缓存/长期未刷新页面），提示强刷后观察至清零
      - `caller` 是区分「遗留调用点」与「未刷新浏览器缓存」的唯一依据：只报命中数无法收敛到入口
      - 按来源聚合：`grep -o "caller=[^ ]*" data/logs/server.log | sort | uniq -c | sort -rn`

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
| PITR（WAL 归档） | RPO 6h → 分钟级 | 方案与演练工具已备（[docs/ops/pitr.md](pitr.md) + `utils/pitr_drill.sh` 链路检查助手）；启用需发布窗口（`archive_mode=on` 需重启）+ 独立归档卷成本确认 |

## 4. 执行记录（逐窗口追加）

| 窗口 | 日期 | 项 1 CSP enforce | 项 2 AES v1 关闭 | 备注 |
|------|------|------------------|------------------|------|
| W0（下一年度收口） | 2026-09-14 | 未闭环（待生产 report 7 天清零；开关与回滚已就位） | 未闭环（待观察窗口内 `aes_v1_decrypt_used` 清零；观测点已交付） | 硬门禁保持：不启动 W1 功能窗口 |
| W9–W10（年度收口复核） | 2026-09-15 | **未闭环**：当日 `CSP violation:` 69 条，09-11 起每日 48~119 条；形态为**合成上报**（logger `xadmin.post`、`AnonymousUser`、`cdn.example.com` / `https://x/`）→ 判据被测试流量污染 | **未闭环**：当日 `aes_v1_decrypt_used` **570 条**（全天分散，20 点仍 96 条）→ 仍有 v1 密文被持续读取 | 硬门禁保持；清零路径见下节「复核结论」 |
| 判据可判化 | 2026-09-16 | **判据已修复（待观察）**：合成上报隔离上线——非真实浏览器来源不再写入 `CSP violation:`，改为 INFO 留痕；起算条件=隔离后重新观察 7 天 | **定位能力已修复（待观察）**：日志新增 `caller=` 调用来源；起算条件=按 caller 收敛来源并清零后关闭开关 | 两项均为「判据/可观测性」修复，非切换本身；切换仍按 §1/§2 前置执行 |

### 复核结论（2026-09-15，W9–W10）

1. **项 1（CSP enforce）**：真实浏览器违规不可判——日志中的违规全部来自 `/api/csp-report` 的合成上报（测试/探测流量，非真实页面），
   因此「连续 7 天清零」在污染消除前无意义。**清零路径**：① 让测试/探测流量与生产日志隔离（测试环境独立日志器或上报端点静音）；
   ② 隔离后重新起算 7 天窗口；③ 窗口内仍命中再按 directive 逐一豁免。
2. **项 2（AES v1 关闭）**：`aes_v1_decrypt_used` 全天持续命中（570 条），说明仍有**旧格式（`Salted__`）密文被反复读取**。
   **清零路径**：① 按命中时刻与访问者定位读取源（系统配置密钥类字段 / 客户端缓存页面）；② 将对应值以 v2 重写（前端 v2 覆盖后重新保存即可）；
   ③ 直到一个完整发布窗口（≥7 天，含全员活跃时段）清零后再置 `SECURITY_AES_V1_DECRYPT_ENABLED=false`。
3. 两项均**不满足切换前置**，本轮不动生产配置；开关与回滚路径保持现状。

### 复核结论（2026-09-16，判据可判化）

1. **项 1（CSP enforce）**：定位到污染源的准确形态——当日 69 条违规与 `tests/unit/common/test_csp.py`
   的 payload 字面量完全一致（`document=https://example.com/#/system/user/index` + `cdn.example.com/x.js`、
   CSP3 信封 `https://x/` + `blob:`、非法 JSON 全空三条），即**探测/测试流量**而非真实浏览器。
   处置：在 `common/api/csp.py` 加合成上报隔离（缺 `document-uri` / 脚本 UA / 非本站文档域 → INFO 不计违规，
   响应头 `X-CSP-Report: ignored`），原始字段仍留痕。`ALLOWED_HOSTS` 为通配或未配置时跳过域名判据（宁可多记）。
   **下一步**：隔离上线后重新起算 7 天窗口，`CSP violation:` 连续 7 天为 0 即可切 enforce。
2. **项 2（AES v1 关闭）**：**澄清一个此前的口径错误**——v1（`Salted__`）只出现在**前端请求体**加密
   （`AESCipherV2`，key 为 username/token 的一次性密文），**不落库**；落库字段级加密是另一套
   `AESCipherV3`（`v3:` 前缀 + HKDF/AES-GCM，`common/base/utils.py:118`），且 `AESCharField/AESTextField`
   全仓无模型使用。因此**不存在「扫库重写 v1 密文」这条路径**，命中必然来自仍在提交旧格式密文的客户端或遗留调用点。
   处置：观测点日志新增 `caller=`（调用方 `文件名:行号 函数名`），使 572 条/天的命中可收敛到具体入口。
   **下一步**：部署后按 `caller` 聚合定位，收敛来源并清零后再置 `SECURITY_AES_V1_DECRYPT_ENABLED=false`。
3. 本轮同样**未切换任何生产配置**：两项的开关与回滚路径保持现状，仅补齐判据与定位能力。
