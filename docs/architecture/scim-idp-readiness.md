# SCIM 真实 IdP 联调准备方案（W4，2026-09-12）

> 背景：SCIM 2.0 目录同步服务端已实现（`system/scim/`，路由 `/api/scim/v2/`，
> 集成测试 `tests/integration/system/test_scim_api.py` 覆盖 provisioning/deprovisioning/
> 改组/审计回溯）。本方案列出接入真实 IdP（Okta / Microsoft Entra ID / Keycloak）前的
> 准备清单，联调本身需外部 IdP 环境与租户权限，另行排期。

## 1. 服务端就绪度核对清单

| 项 | 现状 | 联调前动作 |
|----|------|-----------|
| Bearer Token 鉴权 | 已实现（SCIM Token 配置） | 为联调租户签发独立 token，禁止复用生产 token |
| /Users 过滤（userName eq） | 已实现 | 用 IdP 的真实 filter 表达式回归（Okta 用 userName eq、Entra 用 externalId） |
| POST /Users（provisioning） | 已实现 | 准备一组带中文 displayName 的测试账号（验证 Unicode 落库） |
| PUT/PATCH /Users（attribute 更新） | PATCH 已实现 | Entra 默认走 PATCH（RFC 7644 op 路径），准备 active=true/false 停启用对拍 |
| /Groups 及成员变更 | 已实现 | 准备部门映射表：IdP group → 本地 dept/role 的对照 CSV |
| 软删除语义 | 对接 SoftDeleteModel | 确认 IdP deprovision 时期望行为：本地停用还是软删除（**需产品决策**） |
| 审计回溯 | OperationLog 记录 SCIM 变更 | 联调时抽查「IdP 改组 → 本地权限变更」的审计链 |

## 2. 环境/网络前置

- IdP 侧回调地址需可达本系统公网或 VPN 入口（SCIM Base URL = `https://<host>/api/scim/v2/`）；
- 联调环境用独立 PostgreSQL schema/库，避免 IdP 批量 provisioning 污染演示数据；
- 准备 `Authorization: Bearer <token>` 抓包对照（IdP 日志 ↔ 本地 OperationLog 双侧对账）。

## 3. 验收口径（沿用既有测试）

1. IdP 建用户 → 本地 `/Users` 出现且角色/部门映射正确；
2. IdP 停用用户 → 本地软删/停用（按 §1 决策），该用户存量 JWT 立即失效；
3. IdP 改组成员 → 本地角色变更生效且权限缓存失效（invalid_user_cache_signal 触发）；
4. 全程 OperationLog 审计链完整可回溯。
