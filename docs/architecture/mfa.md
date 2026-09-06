# MFA / 敏感操作二次验证

> 设计对标 JumpServer 的 `UserConfirmation.require(ConfirmType.MFA)` 模式，并结合本项目
> JWT 无状态认证改造：确认状态存 Redis（而非 session）。参考测试：`tests/integration/test_mfa_api.py`。

## 一、能力概览

| 能力 | 说明 |
|------|------|
| 敏感操作二次验证 | 任意 DRF API 一行声明即成为"敏感操作"，未验证返回 HTTP 412，验证通过后有效期内免重复验证 |
| 验证方式（可配置） | `otp`（TOTP 动态口令）/ `sms`（短信验证码）/ `email`（邮件验证码）/ `password`（登录密码），后台设置页可启停 |
| 登录 MFA | 绑定 OTP 的用户登录时强制二次验证（`mfa_required` + 一次性 `mfa_token`，通过后才签发 JWT） |
| OTP 绑定管理 | 个人中心发起绑定（otpauth URI 渲染二维码）→ 动态码确认 → 解绑（本身即敏感操作） |

## 二、核心设计

```
mfa/
├── backends/            # 验证方式后端（策略模式，可插拔）
│   ├── base.py          #   BaseMFA 抽象：check_code / send_challenge / is_active / global_enabled
│   ├── otp.py           #   pyotp TOTP（防重放：同码在窗口期内一次性）
│   ├── sms.py           #   复用 SendAndVerifyCodeUtil（挑战型：服务端先下发）
│   ├── email.py         #   复用 SendAndVerifyCodeUtil（挑战型）
│   └── password.py      #   user.check_password
├── const.py             # ConfirmType：PASSWORD(级别1) < MFA(级别2)
├── confirm.py           # UserConfirmation 权限工厂 / ensure_user_confirmed / 装饰器
├── cache.py             # 确认状态 / OTP 绑定候选密钥 / OTP 已用码（Redis）
├── exceptions.py        # MFAConfirmRequired（HTTP 412 统一协议）
├── services.py          # 对外契约层（其他 app 只允许 import 本模块）
└── views/urls           # /api/mfa/confirm* 与 /api/mfa/otp*
```

关键语义：

- **级别可升级**：`password`（级别 1，短有效期 300s）< `mfa`（级别 2，默认 3600s）。
  MFA 级别方式（otp/sms/email）验证通过后同时满足 password 级别要求；反之不成立。
  低级别敏感操作也允许用户选择高级别方式验证。
- **状态存 Redis 而非 session**：项目为 JWT 双 Token 无状态认证，确认状态按
  `mfa_confirm_state_{user_id}` 存 Redis，记录 `{level, type, method, time}`，
  有效期按确认时使用的方式对应 TTL 计算。
- **防爆破**：验证失败统一计入 `MFABlockUtils`（`_MFA_LIMIT_/BLOCK_` 缓存键），
  达到 `SECURITY_LOGIN_LIMIT_COUNT` 后锁定。

## 三、业务接入方式（三选一）

### 1. DRF ViewSet / APIView（推荐）

```python
from mfa.confirm import UserConfirmation
from mfa.const import ConfirmType

class SecretViewSet(BaseModelSet):
    permission_classes = [IsAuthenticated, UserConfirmation.require(ConfirmType.MFA)]
    # 高敏感操作用 ConfirmType.MFA（otp/sms/email）；一般操作用 ConfirmType.PASSWORD
```

未通过验证时框架抛出 412，无需写任何校验代码；验证在 TTL 内的请求直接放行。
也可用 `get_permissions()` 按 action 差异化声明。

### 2. 函数/方法内手动校验

```python
from mfa.confirm import ensure_user_confirmed
from mfa.const import ConfirmType

def export_secret(request):
    ensure_user_confirmed(request, ConfirmType.MFA)
    ...
```

### 3. 装饰器

```python
from mfa.confirm import require_user_confirmation
from mfa.const import ConfirmType

@require_user_confirmation(ConfirmType.PASSWORD)
def reset_user_api_key(request, user_id):
    ...
```

## 四、前端对接契约（客户端已实现，见下方说明）

### 敏感操作验证弹窗流程

1. 请求敏感 API，收到 HTTP 412：

```json
{"code": 412, "status": 412, "type": "user_confirm_required",
 "confirm_type": "mfa", "detail": "该操作需要进行身份二次验证", "requestId": "..."}
```

2. `GET /api/mfa/confirm?confirm_type=mfa` → 渲染验证弹窗：

```json
{"data": {
  "confirm_type": "mfa",
  "methods": [
    {"name": "otp", "display_name": "OTP 动态验证码",
     "placeholder": "请输入 6 位动态验证码", "challenge_required": false},
    {"name": "sms", "display_name": "短信验证码", "...": "...", "challenge_required": true}
  ],
  "confirmed": false,
  "expire_at": null
}}
```

3. `challenge_required=true` 的方式先调 `POST /api/mfa/confirm/send-code`（body: `{"method": "sms"}`）；
4. `POST /api/mfa/confirm`（body: `{"confirm_type": "mfa", "method": "otp", "code": "123456"}`）
   → `data.expire_at` 为确认到期时间戳，前端可展示倒计时。

### 登录 MFA 流程

1. `POST /api/system/login/basic` 返回（**不签发 JWT**）：

```json
{"data": {"mfa_required": true, "mfa_token": "...",
          "methods": [{"name": "otp", ...}, ...]}}
```

2. 挑战型方式调 `POST /api/system/login/mfa/send-code`（`mfa_token` + `method`）；
3. `POST /api/system/login/mfa/verify`（`mfa_token` + `method` + `code`）
   → 通过后签发 `{access, refresh, *_token_lifetime}`；`mfa_token` 一次性，5 分钟有效。

### xadmin-client 客户端已实现部分

- **`src/api/mfa.ts`**：confirm / send-code / otp 绑定管理 / 登录 MFA 全部接口封装；
- **`src/components/ReMfaConfirm/`**：全局二次验证对话框（命令式 `confirmMfa(confirmType)`，
  并发 412 共享同一弹窗）；
- **`src/utils/http/index.ts`**：响应拦截层统一处理 412 —— 识别 `type=user_confirm_required`
  后唤起验证弹窗，验证通过**自动重发原请求**（`_mfaRetried` 防递归），取消则按普通错误
  reject。业务代码无需感知二次验证的存在；
- **登录页**（`login/components/mfa.vue`）：密码登录返回 `mfa_required` 时切换为动态码
  验证步骤，验证通过后走正常登录跳转；`loginByUsername` 对 `mfa_required` 载荷不写入 token；
- **个人中心**（`user/info/components/mfa.vue`）：新增"MFA 安全"标签页，支持 OTP 扫码
  绑定（`ReQrcode` 渲染 otpauth URI + 手动输入密钥备用）与解绑（解绑请求经全局 412 拦截
  弹验证窗后自动完成）；
- **i18n**：`locales/{zh-CN,en}.yaml` 新增 `mfa.*` 文案（验证方式/输入框文案由后端
  `methods` 元数据下发，天然随服务端语言走）。

## 五、配置项

默认值在 `server/conf.py`（`Config.settings`），映射在 `server/settings/setting.py`，
后台"系统设置 → 安全设置 → MFA 二次验证"（`/api/settings/mfa/auth`）可在线修改：

| 配置 | 默认 | 说明 |
|------|------|------|
| `SECURITY_MFA_CONFIRM_ENABLED` | `True` | 敏感操作二次验证总开关（应急关闭） |
| `SECURITY_MFA_CONFIRM_BACKENDS` | 四种全开 | 允许的验证方式列表 |
| `SECURITY_MFA_VERIFY_TTL` | `3600` | MFA 方式确认有效期（秒） |
| `SECURITY_MFA_PASSWORD_CONFIRM_TTL` | `300` | 密码方式确认有效期（秒） |
| `SECURITY_MFA_LOGIN_PROTECT_ENABLED` | `True` | 绑定 OTP 的用户登录时强制二次验证 |
| `SECURITY_MFA_LOGIN_TOKEN_TTL` | `300` | 登录 MFA 临时令牌有效期（秒） |
| `SECURITY_MFA_OTP_VALID_WINDOW` | `1` | TOTP 容错周期数 |
| `SECURITY_MFA_OTP_ISSUER` | `XAdmin` | otpauth URI 签发方名称 |

## 六、内置敏感操作接入点

以下系统内的高危操作已声明为敏感操作（未验证时统一 412，客户端自动弹验证窗）：

| 操作 | API | 验证级别 |
|------|-----|----------|
| 修改密码（个人中心） | `POST /api/system/userinfo/reset-password` | password |
| 绑定/换绑邮箱、手机 | `POST /api/system/userinfo/bind` | password |
| 删除用户（单删/批量删） | `DELETE /api/system/user/{pk}`、`POST /api/system/user/batch-destroy` | password |
| 解绑 OTP（个人） | `POST /api/mfa/otp/disable` | password |
| 管理员重置用户 MFA | `POST /api/system/user/{pk}/reset-mfa` | password |

状态生命周期：

- 验证通过 → 确认状态写入 Redis（按用户维度），有效期内免重复验证；
- **登出** → 清除确认状态（不依赖认证方式，session/JWT 均覆盖）；
- **修改密码 / 换绑手机邮箱** 成功 → 清除确认状态（可用验证方式已变化）；
- **管理员重置用户 MFA**（`reset-mfa`）→ 清除目标用户 OTP 与确认状态，并解除其
  MFA 验证锁定，用于用户丢失 OTP 后的运营兜底（用户管理页"重置MFA"按钮）；
- 登录 MFA：用户已开启但无任何可用验证方式时（如管理员关闭全部方式）**降级放行**，
  避免登录死锁，同时记录 warning 日志。

## 七、扩展新验证方式

1. 在 `mfa/backends/` 新建后端继承 `BaseMFA`，实现 `check_code`（挑战型再实现 `send_challenge`）；
2. 注册进 `mfa/backends/__init__.py` 的 `MFA_BACKEND_CLASSES`；
3. 在后台设置页（或 `SECURITY_MFA_CONFIRM_BACKENDS`）启用该方式名称。

`global_enabled` 返回 `False`（如 sms 依赖 `SMS_ENABLED`）或 `is_active` 不满足
（如 sms 要求用户已填手机号）的方式会自动从可选列表隐藏。

## 八、注意事项

- `/api/mfa/` 已加入 `PERMISSION_WHITE_URL`：登录用户即可访问（个人安全操作，不走菜单权限）；
  若新增管理类端点请勿复用该前缀白名单。
- 登录 MFA 触发前提是用户 `mfa_level=1`（OTP 绑定成功时自动置位），解绑后自动关闭。
- 验证码登录（短信/邮箱验证码直接登录）本身已是动态因子，不再触发登录 MFA。
- `mfa` 已纳入 `scripts/check_cross_app_imports.py` 门禁，其他 app 引用本 app 能力只能
  `from mfa.services import ...`。
