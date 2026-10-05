#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : custom
# author : ly_13
# date : 11/14/2024

from ..const import CONFIG
from .base import INSTALLED_APPS

# 访问白名单配置，无需权限配置, key为路由，value为列表，对应的是请求方式， * 表示全部请求方式, 请求方式为大写
PERMISSION_WHITE_URL = {
    "^/api/system/login$": ["*"],
    "^/api/system/logout$": ["*"],
    "^/api/system/userinfo$": ["GET"],
    "^/api/system/routes$": ["*"],
    "^/api/system/dashboard/": ["*"],
    # 结构元数据枚举（S7 口径文档化）：choices / search-fields / dict.items 对所有
    # 已登录用户开放，不要求菜单权限——前端表单枚举、通用搜索、引用字段渲染在任意页面
    # 都可能跨菜单消费这些元数据，按菜单收紧会直接破坏渲染。
    # 数据边界：仅返回「字段名 / 枚举 label / 字典项」等结构元数据，不含业务记录行；
    # 如将来需收紧：按 app 白名单放行，或要求对应模型 list 权限（前端需为
    # useDict / search-fields 请求补齐菜单上下文后同步改造，属独立改造项）。
    "^/api/.*choices$": ["*"],
    "^/api/.*search-fields$": ["*"],
    "^/api/system/dict/items$": ["GET"],  # 数据字典消费端（前端 useDict 下拉），同 choices 口径
    "^/api/common/resources/cache$": ["*"],
    "^/api/notifications/site-messages/unread$": ["*"],
    # MFA / 敏感操作二次验证（3.3 收敛）：前缀通配改为精确端点 + 最小方法集——
    # 个人安全操作无需菜单权限（视图内要求登录态），但新增端点不应自动豁免。
    # 端点清单随 mfa/urls.py 的注册表（confirm / otp 两视图集，均 NoDetail 无 {pk} 路由）
    "^/api/mfa/confirm$": ["GET", "POST"],  # GET=查询确认状态 / POST=发起二次确认
    "^/api/mfa/confirm/send-code$": ["POST"],
    "^/api/mfa/otp$": ["GET"],
    "^/api/mfa/otp/close$": ["POST"],
    "^/api/mfa/otp/confirm$": ["POST"],
    "^/api/mfa/otp/disable$": ["POST"],
    "^/api/mfa/otp/open$": ["POST"],
    "^/api/mfa/otp/recovery-codes$": ["GET"],  # 恢复码剩余数量
    "^/api/mfa/otp/recovery-codes/regenerate$": ["POST"],
    "^/api/mfa/otp/start$": ["POST"],
    "^/api/mfa/otp/test$": ["POST"],
    "^/api/system/personal-access-tokens": ["*"],  # 个人访问令牌（PAT），个人凭证个人管，同 MFA 口径
    # Passkey 凭据：个人凭据个人管，视图内收口为本人（超管可查全量），同 PAT/MFA 口径
    "^/api/system/passkeys": ["*"],
    # 列表「我的视图」：个人筛选偏好，视图内收口为「本人 + 共享只读」，同 PAT 口径
    "^/api/system/saved-views": ["*"],
    # 登录前 Passkey 挑战值（匿名，凭一次性 mfa_token）：与登录流程同级，不参与菜单权限
    "^/api/system/login/mfa/passkey/": ["*"],
    # 退出用户模拟：被模拟用户未必有任何菜单权限，退出模拟是安全阀必须无条件可达
    # （POST，视图内以登录态 + token claim imp 收口，见 views/auth/impersonation.py）
    "^/api/system/impersonate/exit$": ["POST"],
    # 应用接口范围选项（API 应用管理页表单枚举，同 choices/search-fields 口径）：
    # 返回的只是「当前用户可授权的接口」元数据（用户自己权限菜单派生，无业务数据行），
    # 且管理页的查看/编辑是两个独立权限点——按菜单收紧会让只有编辑权限的用户打不开勾选器。
    "^/api/system/api-applications/scope-options$": ["GET"],
    # 应用资源授权目录（四级授权表单枚举，同 scope-options 口径：只返回可授权元数据）
    "^/api/system/api-applications/grant-options$": ["GET"],
    # 第三方登录与绑定：登录前置（authorize/callback）必须匿名可达，绑定管理是个人凭证，
    # 两者都无需菜单权限（视图内自行要求 DRF IsAuthenticated，见 views/auth/oauth.py）
    "^/api/system/auth/oauth/": ["*"],
    # 开放平台换发端点：凭 client_secret 认证（凭证即身份），视图内 fail-closed
    "^/api/system/open/token$": ["*"],
    # OAuth 授权码端点：授权码换发（token/revoke）同客户端凭证口径；
    # authorize/approve 需登录态但不需要菜单权限（第三方接入点，视图内 fail-closed）
    "^/api/system/open/oauth/": ["*"],
}

# 前端权限路由 忽略配置
ROUTE_IGNORE_URL = [
    "^/api/system/.*choices$",  # 每个方法都有该路由，则忽略即可
    "^/api/.*search-fields$",  # 每个方法都有该路由，则忽略即可
    "^/api/.*search-columns$",  # 该路由使用list权限字段，无需重新配置
    "^/api/settings/.*search-columns$",  # 该路由使用list权限字段，无需重新配置
    "^/api/system/dashboard/",  # 忽略dashboard路由
    "^/api/system/captcha",  # 忽略图片验证码路由
    "^/api/mfa/",  # 忽略 MFA 二次验证路由
    "^/api/system/api-applications/scope-options$",  # 接口范围选项：白名单元数据，无需再配权限点
    "^/api/system/api-applications/grant-options$",  # 资源授权目录：白名单元数据，无需再配权限点
    "^/api/system/passkeys",  # Passkey 个人凭据：白名单路由，无需再配权限点
    "^/api/system/saved-views",  # 我的视图：白名单路由，无需再配权限点
]

# 访问权限配置
PERMISSION_SHOW_PREFIX = [
    r"api/system",
    r"api/approval",
    r"api/ai",
    r"api/dataset",
    r"api/settings",
    r"api/notifications",
    r"api/flower",
    r"api-docs",
]
# 数据权限配置：登记在此的 app 模型进入「数据权限」规则选择器的表树
# （get_app_model_fields 重建 DATA 树）；dataset 的加入用于「表单数据」管理端
# 列表（dataset.dynamicformsubmission 行级可见域），未配置授权的模型零影响。
# approval / ai 是 3.1 批次自 system 拆出的 app：模型原以 system.* 前缀在表树内，
# 拆分后未回归导致规则选择器看不到审批/AI 模型（写入校验与读侧编译均按 table
# 现算，表树只是选择器数据源，登记即恢复拆分前的可选面）。
# demo 按需注入：仅当 demo 应用实际安装（config.yml XADMIN_APPS）时
# 进入表树——Book 示例的数据/字段权限是文档化演示场景（demo/README.md），内置
# 种子含 demo.book 字段树，缺席会让「字段同步」把 demo 子树清掉。生产裁剪 demo
# 无需再手工清理本清单（路由装配期 auto_register_app_url 的注入与本次装配期
# 注入重复，消费侧仅做成员判断，无害）。
PERMISSION_DATA_AUTH_APPS = ["identity", "file", "system", "settings", "notifications", "dataset", "approval", "ai"]
if "demo" in INSTALLED_APPS:
    PERMISSION_DATA_AUTH_APPS.append("demo")

API_LOG_ENABLE = CONFIG.API_LOG_ENABLE
API_LOG_METHODS = CONFIG.API_LOG_METHODS  # 'ALL'

# 忽略日志记录, 支持model 或者 request_path, 不支持正则
API_LOG_IGNORE = CONFIG.API_LOG_IGNORE

# 在操作日志中详细记录的请求模块映射
API_MODEL_MAP = CONFIG.API_MODEL_MAP
