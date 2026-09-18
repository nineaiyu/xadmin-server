# LDAP / AD 真实目录联调准备方案

> 背景：LDAP bind 认证与目录同步已实现（`system/ldap/`；管理页 `settings/views/ldap.py`；
> 周期任务 `sync_ldap_directory_job`，每小时 :17 由 `LDAP_SYNC_ENABLED` 控制启停）。
> 本方案列出接入真实 LDAP/AD 前的准备清单与验收口径——**联调本身需外部目录环境与
> 只读服务账号，另行排期**（归属长期优化方案 §4.1 LDAP 行的 P3 联调清单化）。
> 姊妹篇：[scim-idp-readiness.md](scim-idp-readiness.md)（SCIM 侧准备清单）。

## 1. 服务端就绪度核对清单

| 项 | 现状 | 联调前动作 |
|----|------|-----------|
| bind 认证接入认证链 | 已实现（`LDAP_AUTH_ENABLED`；优先级 `local_first` / `ldap_first`，目录不可用时降级不阻断本地登录） | 准备「本地同名」「目录独有」两类账号各一，验证优先级与降级 |
| 自动建号 | 认证侧 `LDAP_AUTH_AUTO_CREATE`、同步侧 `LDAP_SYNC_AUTO_CREATE`（默认开） | 确认是否允许目录账号自动落库（及首次登录即建号的合规口径） |
| OU → 部门树 | 已实现（`LDAP_DEPT_ENABLED` + `LDAP_DEPT_SEARCH_BASE`；部门 code 带命名空间，不碰手工部门） | 准备测试 OU 子树（≥3 层、含中文名），确认挂靠「最近祖先部门」的预期 |
| 组 → 角色映射 | 已实现（`LDAP_GROUP_ROLE_MAP`：组 DN/CN → 角色 code；只增删映射角色，不动手工授权） | 准备组 ↔ 角色对照表（CSV），确认映射键用 DN 还是 CN |
| 属性映射 | `sAMAccountName` / `cn` / `mail` / `telephoneNumber`（四项均可配） | 对照目标目录的实际 schema 校正（AD 与 OpenLDAP 属性名不同） |
| 冲突处理与审计 | 用户名冲突跳过并落审计；邮箱冲突清空重绑 | 准备同名、同邮箱两类冲突样例，抽查 OperationLog 回溯 |
| 凭据保护 | `LDAP_BIND_PASSWORD` 值级加密存储 | 联调用**只读服务账号**，禁止域管；结束后轮换密码 |
| 缺失成员策略 | `LDAP_SYNC_MISSING_POLICY` = `deactivate` / `soft_delete` / `ignore` | **需产品决策**：目录移除成员时期望停用、进回收站还是不动 |
| 定时同步 | 每小时 :17（非实时）；管理页可随时停用 | 确认可接受的同步延迟；联调期先用管理页手动 run 触发核对 |
| 连接自检 | 管理页「测试连接」返回用户/部门计数（`LDAP_CONNECT_TIMEOUT` 10s） | 联调第一步先跑自检（URI/凭据/BaseDN 三件套） |
| 编码与分页 | 按 `LDAP_SYNC_PAGE_SIZE`（默认 500）分页拉取 | 大目录（>5000 人）联调时观察分页耗时与超时 |

## 2. 环境 / 网络前置（需外部提供）

- 目录地址与协议：`ldap://` 或 `ldaps://`（自签证书请一并提供 CA）；启用 STARTTLS 时注明；
- 只读服务账号：bind DN + 密码（写入值级加密；联调后请轮换）；
- 用户搜索范围：`LDAP_USER_SEARCH_BASE` 与 `LDAP_USER_FILTER`（默认 `(objectClass=person)`）；
- 部门搜索范围：`LDAP_DEPT_SEARCH_BASE`（启用 OU→部门树时必填）；
- 测试数据：1 个 ≥3 层 OU 子树、1 个用于角色映射的组、1 个待停用/移出 OU 的账号；
- 网络可达：应用侧可直连目录端口（389/636）；经 VPN/白名单时提前放行。

## 3. 验收口径（联调时逐项对拍）

1. **bind 登录**：目录账号凭密码登录成功（首次自动建号）；本地同名账号按优先级策略验证；
2. **停用/移出**：目录侧禁用或移出搜索范围的账号，按缺失策略处理；停用后该账号的登录与存量凭证在下次校验时被拒（认证链校验账号在用状态）；
3. **OU 变更**：新增/改名/移动 OU → 本地部门树同步，用户挂到最近祖先部门；
4. **组变更**：加组/退组 → 映射角色挂撤生效（非映射角色不受影响）；
5. **冲突与审计**：同名/同邮箱样例按既有策略处理且 OperationLog 可回溯；
6. **同步摘要**：管理页可见本轮新增/更新/停用计数与通知（无变更不打扰）。

## 4. 本地 mock 验证（本仓已覆盖，无需外部环境）

| 覆盖点 | 测试 |
|--------|------|
| bind 认证链 / 优先级 / 目录不可用降级 | `tests/unit/system/test_ldap_auth.py` |
| 连接与分页 / 绑定失败 | `tests/unit/system/test_ldap_client.py` |
| 用户同步 / OU→部门树 / 缺失策略三态 / 冲突审计 / 通知 / 连接自检 | `tests/unit/system/test_ldap_sync.py` |
| 组→角色映射挂撤（映射角色专属） | `tests/integration/system/test_ldap_group_role.py` |
| 管理页配置读写 | `tests/integration/system/test_ldap_api.py` |

**结论**：§3 的 1–6 口径已在桩目录（fake 连接注入）下断言通过；真实目录联调的主要
剩余工作是**参数校正**（属性名/过滤器/搜索范围）与**规模验证**（分页、耗时、超时），
以及 §1 中标注「需产品决策」的缺失成员策略确认。