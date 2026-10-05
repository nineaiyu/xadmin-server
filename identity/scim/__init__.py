# -*- coding: utf-8 -*-
"""SCIM 2.0 用户目录同步（S1，RFC 7644 Users/Groups 核心子集）。

与既有身份体系的衔接：
- **鉴权**：独立 Bearer Token（`SCIM_TOKEN`，与 JWT/PAT 完全分离，见 auth.py），
  另有限流与审计（OperationLog.auth_type=scim）；
- **用户**：映射既有 username 策略（唯一性按 `all_objects` 校验，含回收站占用）；
- **会话**：停用/删除即 `force_logout_user`（令牌失效时间戳 + refresh 拉黑 + WS 踢线 +
  UserSession 置离线），与在线用户管理同一套失效链路；
- **身份联邦**：SCIM 负责「开通/停用/改组」，登录仍走既有 JWT/OAuth2-OIDC，
  SCIM 不下发凭据（payload 无 password 时账号置不可用密码，只能经联邦登录）。
"""
