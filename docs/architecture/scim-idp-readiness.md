# SCIM 真实 IdP 联调准备方案（W4，2026-09-12）

> 背景：SCIM 2.0 目录同步服务端已实现（`system/scim/`，路由 `/api/scim/v2/`，
> 集成测试 `tests/integration/system/test_scim_api.py` 覆盖 provisioning/deprovisioning/
> 改组/审计回溯）。本方案列出接入真实 IdP（Okta / Microsoft Entra ID / Keycloak）前的
> 准备清单，联调本身需外部 IdP 环境与租户权限，另行排期。

## 1. 服务端就绪度核对清单

| 项 | 现状 | 联调前动作 |
|----|------|-----------|
| Bearer Token 鉴权 | 已实现（SCIM Token 配置） | 为联调租户签发独立 token，禁止复用生产 token |
| /Users 过滤（userName eq） | 已实现（**仅 `userName` / `id`**） | Entra 联调时把用户匹配属性配为 `userName`；`externalId` 过滤在 Users 上明确返回 400 invalidFilter（Groups 支持 externalId），本地 mock 已固化该边界 |
| POST /Users（provisioning） | 已实现 | 准备一组带中文 displayName 的测试账号（验证 Unicode 落库） |
| PUT/PATCH /Users（attribute 更新） | PUT 与 PATCH（RFC 7644 op 路径）均已实现 | 准备 active=true/false 停启用对拍（Okta 与 Entra 均以 PATCH 为主） |
| /Groups 及成员变更 | 已实现（add / replace / remove，含 `members[value eq "..."]` 形态；成员值支持主键或 username 直传） | 准备部门映射表：IdP group → 本地 dept/role 的对照 CSV |
| deprovision 语义 | DELETE = 停用（`is_active=False`，保留审计与历史），并即时失效该用户存量 JWT 请求 | 确认 IdP 停用时期望行为与本地一致；回收站软删留作本地治理手段（**需产品决策**） |
| 审计回溯 | OperationLog 记录 SCIM 变更 | 联调时抽查「IdP 改组 → 本地权限变更」的审计链 |

## 2. 环境/网络前置

- IdP 侧回调地址需可达本系统公网或 VPN 入口（SCIM Base URL = `https://<host>/api/scim/v2/`）；
- 联调环境用独立 PostgreSQL schema/库，避免 IdP 批量 provisioning 污染演示数据；
- 准备 `Authorization: Bearer <token>` 抓包对照（IdP 日志 ↔ 本地 OperationLog 双侧对账）。

## 3. 验收口径（沿用既有测试）

1. IdP 建用户 → 本地 `/Users` 出现且角色/部门映射正确；
2. IdP 停用用户 → 本地停用（按 §1 决策），该用户存量 JWT 立即失效；
3. IdP 改组成员 → 本地角色变更生效且权限缓存失效（invalid_user_cache_signal 触发）；
4. 全程 OperationLog 审计链完整可回溯。

## 4. 本地 mock 验证（本仓已覆盖，无需外部租户）

`tests/integration/system/test_scim_api.py` 按 IdP 请求形态分三层固化：

| 覆盖点 | 用例 |
|--------|------|
| 鉴权与能力声明（token 分离 / 限流 / ServiceProviderConfig） | `TestScimAuthAndConfig` |
| Users 生命周期（建/查/改/PUT/PATCH/停用踢会话/DELETE 停用/审计） | `TestScimUsers` |
| Groups 生命周期（externalId 匹配、成员增删、删组回收角色） | `TestScimGroups` |
| **Okta 风格**（name 对象 + displayName + emails.primary；停用 PATCH 幂等重放） | `TestIdpFlavorMock.test_okta_style_payload_and_deactivation` |
| **Entra 风格**（组 externalId 过滤 + 成员 username 直传 + §3.5.2.2 remove 形态） | `TestIdpFlavorMock.test_entra_style_group_member_ops` |
| Users externalId 过滤的明确边界（400 invalidFilter，联调前可知） | `TestIdpFlavorMock.test_users_external_id_filter_is_explicit_boundary` |

**结论**：§3 的 1–4 口径已在本地 mock 下断言通过；真实 IdP 联调的主要剩余工作是
租户侧配置（Base URL / 独立 token / 匹配属性=userName）与 §1 中标注「需产品决策」
的 deprovision 语义确认。
