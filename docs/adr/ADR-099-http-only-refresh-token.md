# ADR-099：refresh token 迁移 HttpOnly Cookie（结构性安全候选）

- 状态：**暂不实施（触发制）**
- 日期：2026-10-30
- 关联：[ADR-001](ADR-001-csrf-jwt-only.md)（JWT-only / CSRF 不启用）、
  [ADR-100](ADR-100-review-retained-decisions.md)（审查保留项处置）、
  `xadmin-client/src/utils/auth.ts`、`packages/xadmin-common/common/core/auth/authentication.py`

## 一、背景

当前浏览器侧认证态由两条非 HttpOnly Cookie 承载：

- `X-Token`（access token，短寿命）与 `X-Refresh-Token`（refresh token，长寿命）；
- Cookie 属性统一为 `SameSite=Lax` + 按 `isSecureContext` 条件 `Secure`；
- 前端 JS 可直接读取两条 Cookie，refresh 由 `utils/http/refreshToken.ts` 单飞调用；
- 后端同时支持 `Authorization` 头与 Cookie 兜底认证——后者是 WS/SSE 无法自定义
  请求头时的必要通道。

第二轮审查将「token 可被 XSS 读取」登记为纵深防御残项（SEC-FE-01 / 池-04）：
项目已用 DOMPurify 白名单、Markdown 关闭原始 HTML、CSP 门禁与后端 bleach 收敛 XSS
面，但一旦出现 XSS 绕过，攻击者可直接读取长期 refresh token，影响窗口远大于 access
token；Cookie 通道另有一层「副作用 GET」残余面（已由 AST 清单门禁守护，当前零命中）。

## 二、决策

**本轮不迁移，维持双 Cookie 现状；把迁移登记为触发制结构性候选，触发后再立项。**
不采用「只改前端把 refresh 放内存」的折中方案：刷新页即丢会话，且无法解决 XSS
读取 access token 的问题；也不在未完成服务端 Cookie 签发/撤销链路前单边改前端。

## 三、触发条件（任一命中即重新评估，先修订本 ADR）

1. 出现可实际利用的 XSS 绕过（CSP/DOMPurify/模板转义任一防线失效），或安全评审/
   合规要求「长期凭据不可被 JS 读取」；
2. 认证链进入下一次结构性改造窗口（WS/SSE 鉴权、OAuth/OIDC 会话、PAT 体系合并等）；
3. access token 寿命被显著延长（超过当前短寿命口径），使 XSS 读取 access token 的
   收益接近 refresh token；
4. 需要支持第三方脚本/低信任嵌入，CSP 必须放宽，现有纵深防御前提不再成立。

## 四、触发后的目标设计与迁移轮廓

| # | 决策点 | 目标 |
|---|--------|------|
| 1 | refresh 承载 | `HttpOnly; Secure; SameSite=Lax/Strict; Path=/api/identity/auth/refresh`，JS 不可读；仅刷新端点接收 |
| 2 | access 承载 | 保持短寿命；优先内存 + `Authorization` 头，WS/SSE 用一次性短期票据或同源 Cookie 兜底（需单独评估） |
| 3 | CSRF | 刷新端点要求自定义头/双提交令牌；因 Cookie 自动携带，端点必须拒绝跨站表单/简单请求 |
| 4 | 轮换与撤销 | refresh 一次性轮换 + 服务端族谱/黑名单；登出/踢出/改密立即失效；复用现有 `refreshToken` 单飞与信号失效链 |
| 5 | 兼容与回滚 | 双写过渡期：新端点签发 HttpOnly，旧 Cookie 停止下发；开关回退到当前双 Cookie 模型 |
| 6 | 验证 | 后端单测 + 前端 auth/WS/SSE 用例 + E2E `auth`/`smoke:fresh`；XSS 场景验证 JS 读不到 refresh |

迁移必须按「契约 → 后端 → 前端 → E2E」顺序，独立 ADR 修订 + 独立发布窗口；不得与
普通业务改动混提。

## 五、后果

- **维持现状的收益**：认证链简单、WS/SSE 直接复用 Cookie、无刷新端点 CSRF 改造；
  现有 XSS 防线已把实际风险压到较低水平。
- **维持现状的代价**：XSS 一旦绕过即可读取长期 refresh token；该风险被显式接受并
  纳入安全评审清单，而不是被遗忘。
- **触发后的代价**：刷新端点、Cookie 属性、WS/SSE 鉴权、前端 token 存储与登出清理
  全链改造，属 L 级；需要一次性发布窗口与回滚开关。
