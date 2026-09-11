# 通知渠道体系（notifications）

> 通知发送的渠道抽象：新增一个渠道只需「一个模块文件 + 一行渲染注册」，无需改动发送管线。
> 本文对应 `notifications/backends/` 与 `notifications/notifications.py`，配置项见 `server/conf.py` 与 `server/settings/setting.py`。

## 一、渠道模型（三件套）

| 组成 | 位置 | 职责 |
|------|------|------|
| `BACKEND` 枚举 | `notifications/backends/__init__.py` | 渠道清单（email / site_msg / sms）；`client` 按名取渠道实现；`filter_enable_backends` 过滤可用渠道 |
| 渠道实现模块 | `notifications/backends/<name>.py` | 暴露模块级 `backend = XxxBackend`（`BackendBase` 子类）；约定式自动加载，**加载失败只告警跳过，不阻断启动** |
| 渲染方法注册 | `notifications/notifications.py` 的 `register_backend_msg` | 声明该渠道消费哪份消息文案（`get_email_msg` / `get_sms_msg` …）；未注册的渠道回退 `get_common_msg` |

## 二、新增一个渠道（示例：企业微信机器人）

```python
# notifications/backends/wecom.py
from .base import BackendBase


class Wecom(BackendBase):
    account_field = "wecom_id"                  # User 上对应的接收账号字段
    is_enable_field_in_settings = "WECOM_ENABLED"  # 渠道总开关（settings 名）

    def send_msg(self, users, message, subject="", **kwargs):
        accounts, unbound, __ = self.get_accounts(users)   # 未绑定账号的用户自动跳过
        if not accounts:
            return
        ...  # 调用渠道 SDK


backend = Wecom  # 约定：模块级 backend 变量
```

再补三处登记：

1. `notifications/backends/__init__.py` 的 `BACKEND` 枚举加一行 `WECOM = "wecom", _("WeCom")`；
2. `server/conf.py` 加默认值（如 `"WECOM_ENABLED": False`）、`server/settings/setting.py` 暴露到 Django settings；
3. 消息文案特殊时，在 `notifications/notifications.py` 加 `register_backend_msg(BACKEND.WECOM, "get_wecom_msg")`
   （渠道文案无特殊要求可不注册，自动回退 `get_common_msg`）。

## 三、渠道可达性（两层过滤）

| 层 | 判定 | 失效行为 |
|----|------|----------|
| 渠道级 `is_enable` | `BackendBase` 读 `settings.<is_enable_field_in_settings>`；开关缺失按禁用兜底 | 渠道不进入发送映射（`get_backend_msg_mapper` / `filter_enable_backends` 均跳过） |
| 用户级 `get_accounts` | 按 `account_field` 把接收人拆成「已绑定 / 未绑定」 | 未绑定用户跳过并发 debug 日志（排查"为什么没收到"先看这里） |

存量订阅数据里的已下线渠道（如 dingtalk）：`BACKEND(...)` 抛 `ValueError` 的取值会**告警跳过**，不会打断发布链路。

## 四、短信通知模板（SMS 渠道）

短信服务商只接受预审批模板，正文以「通知模板 + 单变量」发送：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `SMS_ENABLED` | `False` | 渠道总开关 |
| `SMS_NOTIFY_SIGN_NAME` | `""` | 通知短信签名；与模板 code 任一为空则渠道自动降级为不可用 |
| `SMS_NOTIFY_TEMPLATE_CODE` | `""` | 通知短信模板（模板需含单变量，变量名见下一项） |
| `SMS_NOTIFY_TEMPLATE_PARAM_KEY` | `content` | 模板变量名（正文填入该变量） |
| `SMS_BACKEND` | `alibaba` | 短信服务商（`common/sdk/sms/`，验证码与通知共用） |

降级语义：开关开启但模板未配置 → `SMS.is_enable()` 返回 `False`，发送链路静默跳过短信渠道，不影响邮件/站内信。

## 五、排错指引

- **没收到邮件/短信**：确认订阅 `receive_backends` 含该渠道 → 渠道级开关 → 用户级 `account_field` 是否绑定（debug 日志 `skip N user(s) without ... bound`）；
- **订阅页渠道列表**：`/api/notifications/` 的 backends 接口只下发 `is_enable` 为真的渠道（`notifications/views/notifications.py`）;
- **发送异常不外抛**：`Message.send_msg` 对单渠道异常打印堆栈并继续其他渠道（`NotImplementedError` 直接跳过）。
