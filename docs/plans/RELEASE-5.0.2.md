# xadmin 5.0.2 发布清单（发版台账）

> 状态行：2026-10-19 五仓 tag 已推送（server / client / web / docs / installer），镜像构建与
> 阿里云同步全部成功；当日追加一次「HTTP 内网部署登录失败」热修，client 与 web 重打 tag
> 重出镜像（server / docs / installer 无改动，沿用原 tag 资产）。
> 上游背景：5.0.1 的安装侧修复已让一键安装跑通，本次暴露的是「装得上但登不进」。

## 一、发布结论

**可以发布**。本版主体是 5.0.1 之后累积的功能批次（公告 Markdown、字段权限种子命令、
存量 4.x 回灌、种子分层等）；另含一处发布后 24h 内在真实部署环境发现的登录阻断缺陷，
已热修并重新出镜像。**无表结构变更**（公告 `message_format` 随 5.0 初始迁移表达）。

## 二、改动清单

| 仓库 | 分支 | 内容 |
|------|------|------|
| `xadmin-server` | main | 公告正文支持 Markdown（`message_format` 字段 + 预览分流 + `convert_notice_markdown` 存量转换）；`sync_field_permissions --update-seed`；存量 4.x 数据回灌与原地结构对齐；内置种子按应用分层；种子装载/收尾批量收敛；`doctor` 自检扩为十项；CHANGELOG 登记 5.0.2 |
| `xadmin-client` | main | 公告 Markdown 编辑器（编辑/分栏/预览三态、`ReSplitPane`、工具栏、按钮/拖拽/粘贴上传）；表单弹窗配置化收敛；AI 配置中心三分栏；**认证 Cookie Secure 改按安全上下文自动判定（热修）** |
| `xadmin-installer` | main | 升级链路接入存量库原地结构对齐；升级流程内置 PostgreSQL 大版本数据目录迁移；v 前缀归一化、备份开关、基线门禁三处修复 |
| `xadmin-web` | main | 无独立改动（随 client 热修重出镜像） |
| `xadmin-docs` | main | 新增功能特性总览；模块裁剪排障表；权限篇补 5.x 角色生效口径；MinIO 快速开始；容器化部署补流程总览与日常命令 |

## 三、验证状态（发布前）

| 面 | 命令 / 方式 | 结果 |
|----|-------------|------|
| 后端静态 | `ruff format --check` / `ruff check` / `mypy` | ✅ 全绿 |
| 后端迁移漂移 | `makemigrations --check --dry-run` | ✅ `No changes detected` |
| 后端文档门禁 | 事实 / 索引 / 路径 / ADR 状态 / 体积 | ✅ 通过（33 条受保护事实，含 `const:VERSION`=5.0.2） |
| 前端单测 | `pnpm test:run` | ✅ 345 文件 / 2342 用例通过（含新增 5 组 Cookie Secure 用例） |
| 前端类型 | `pnpm typecheck` | ✅ 通过 |
| 前端 lint | `pnpm lint`（eslint + prettier + stylelint） | ✅ 通过 |
| 前端门禁 | i18n / bundle-size / version / contract / contract-usage / menu-permissions / as-unknown / module-cycles / success-code / entry-closure | ✅ 全绿（首屏闭包 463.5 KB，代码账本 +5.8 KB / i18n +5.4 KB，均在 15 KB 预算内） |

### 热修的本地容器验证（2026-10-19）

发布后在真实 HTTP 部署（`http://192.168.0.109`）发现「登录成功即未授权 → 跳 `/error/500`」。
因目标虚拟机随后不可用，改在本地以同形态容器复现验证：

| 项 | 证据 |
|----|------|
| 复现环境 | 本地起 `xadmin-web:5.0.2` 容器，经局域网 IP `http://192.168.36.115:18999` 访问，`window.isSecureContext === false`（与失败现场同形态） |
| 旧逻辑对照 | 同一上下文写入 `secure:true` 的 Cookie → `cookieStored: false`（浏览器静默丢弃） |
| 新逻辑 | 同一上下文按 `isSecureContext` 判定 → `secure=false` → `cookieStored: true` |
| 编译产物核对 | 镜像内 chunk 为 `secure:dt()`，`dt(){return typeof window<"u"&&window.isSecureContext===!0}`，非硬编码 |
| 后端连通性 | 容器挂到本地栈网络后 `/api/common/api/health` 四项全 `true` |

> 说明：本地后端 `SECURITY_LOGIN_CAPTCHA_ENABLED=True`，无法免验证码跑通完整登录；
> 故以「Cookie 写入行为 A/B 对照 + 编译产物核对」作为该修复的验收口径。完整浏览器登录
> 流程待目标机恢复后补测。

## 四、发布步骤（tag 顺序）

1. `v5.0.2`（`xadmin-server`）：版本门禁（tag ↔ `const.py` ↔ client `package.json`）→ 依赖审计
   + 镜像扫描 → 多架构构建推送 → SBOM / OpenAPI 上传 Release。
2. `v5.0.2`（`xadmin-client`）：构建并推送 `nineaiyu/xadmin-client:5.0.2`。
3. `v5.0.2`（`xadmin-web`）：等待 client 镜像后产出 `nineaiyu/xadmin-web:5.0.2`。
4. `v5.0.2`（`xadmin-docs`）：产出 `nineaiyu/xadmin-docs:5.0.2`。
5. `v5.0.2`（`xadmin-installer`）：安装包资产（`static.env` 的 `VERSION=5.0.2`）→ 镜像同步。

**热修追加（client + web 重打 tag）**：删除旧 `v5.0.2` tag → 重新指向热修提交 → 触发构建 →
web 基于新 client 镜像重建 → `gh workflow run sync-image-mirror.yml -f version=5.0.2` 补跑同步。
server / docs / installer 的 tag 与资产不受影响（无改动）。

## 五、发布后验证

1. 五个 tag 均已推送，各仓 `Build and Push Image` / `Create Release` / `scripts-check` /
   `sync-image-mirror` 全部 success；
2. 阿里云仓 `server` / `web` / `docs` 的 `5.0.2` 均存在，各含 `linux/amd64` + `linux/arm64`；
3. server Release 资产：`openapi.json` / `sbom-server.cdx.json` / `quick_start.sh`；
   installer 包内 `static.env` 为 `VERSION=5.0.2`，且 5.0.1 遗留三处升级修复均在包内。

## 六、已知边界与遗留

- **完整登录流程未在真实机器复测**：目标虚拟机（192.168.0.109）在热修验证中途不可达，
  浏览器端「账号 + 验证码 → 进后台」的闭环留待机器恢复后补跑；本地已用 A/B 对照证明
  Cookie 写入行为正确、编译产物正确、后端连通正常。
- **`ops/dev_up.sh` 快速体验本就正常**：dev server 下 `import.meta.env.PROD` 为 false，
  从不带 Secure；本次缺陷只影响发布镜像的 HTTP 部署形态。
- **建议后续在 E2E 增加一条「非安全上下文登录」守护**：现有 E2E 跑在 localhost（安全上下文），
  覆盖不到该分支；可按 `E2E_FRONT_PORT` + 局域网 IP 形态补一条用例。
