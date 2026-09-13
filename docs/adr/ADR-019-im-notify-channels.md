# ADR-019：企业 IM 消息渠道（钉钉 / 企业微信 / 飞书）

- 状态：已接受
- 日期：2026-09-12
- 关联：年度开发计划 2026.10-2027.09 §四 W3（G2b）；ADR-018（IM 扫码登录，
  本 ADR 的账号来源）；`docs/architecture/notification-channels.md`（渠道三件套
  模板，本 ADR 按其落地）；既有渠道 email / site_msg / sms

## 背景

通知渠道抽象（notifications）已有 email / 站内信 / 短信三渠道，新增渠道的
路径是「一个模块文件 + 一行渲染注册」。企业 IM 消息渠道（计划 G2b）让审批/
告警/同步摘要等通知可推送到钉钉、企业微信、飞书；与 G2a 扫码登录天然衔接
——用户经 IM 登录后即留下身份绑定，通知渠道据此触达。

三家发送协议：钉钉「工作通知 asyncsend_v2」（需 userid + agent_id，且只有
unionId）；企微「应用消息 message/send」（touser=userid）；飞书
「im/v1/messages」（receive_id 支持 union_id）。

## 决策

### 1. 账号映射复用 `UserOAuthBinding`（按 flavor 归集），不新增用户字段

- 通知触达账号 = 该用户在对应 IM 的身份绑定：`UserOAuthBinding` 里 provider
  的 **flavor 匹配**该渠道的所有绑定（管理员自定义 provider key 不影响投递）。
  后端 `get_accounts` 覆写为一次批量查询（`provider__in`）拆「已绑定/未绑定」，
  未绑定用户沿用既有 debug 日志口径；
- 三家的身份可用性：企微 binding.subject 即 userid 直发；飞书以
  `receive_id_type=union_id` 直发；钉钉工作通知只认 userid，发送前按
  `topapi/user/getbyunionid` 换取并缓存（django cache，与 token 同生命周期
  管理）；
- **明示边界**：从未经对应 IM 登录的用户不可达该渠道（无绑定，静默跳过）；
  不提供手工填写 IM 账号的入口（unionId/userid 语义易混，且 G2a 登录即自动
  建绑定，零维护）。候选池可再评估「管理员代录」需求。

### 2. 发送 SDK 收口 `common/sdk/im/`，与 `common/sdk/sms/` 对称

- 三个客户端模块（dingtalk / wecom / feishu）：corp/tenant token 获取与缓存
  （django cache，TTL 取 expires_in - 120，凭据摘要入 key，改密自动换 key）、
  unionId→userid、按账号发文本消息；http 客户端可注入，单测完全离线；
- 错误语义各家包裹不同（企微/钉钉 `errcode`、飞书 `code`），统一在 SDK 层
  判定并抛带可读信息的异常；`Message.send_msg` 既有 per-channel 异常隔离
  保证单渠道故障不影响其他渠道；
- 消息渲染注册 `register_backend_msg(渠道, "get_text_msg")`：复用 Message
  基类的 HTML→纯文本转换，subject + 正文拼为文本消息，三家共用，不新增
  渲染方法。

### 3. 配置面走 Setting 体系（category=`notify_im`），默认全关

- 每渠道：开关 + 应用三元组（钉钉 appKey/appSecret/agentId、企微 corpId/
  corpSecret/agentId、飞书 appId/appSecret），secret 字段 `write_only` ⇒
  值级加密落库、API 永不回传；默认值登记 `server/conf.py` 并在
  `server/settings/setting.py` 暴露；
- `is_enable` = 渠道开关 **AND** 凭据齐全（沿用 SMS 渠道降级语义：开关开而
  凭据缺 → 渠道不可用，发送链路静默跳过，不影响邮件/站内信）；
- 管理页挂「消息通知设置」新页签（复用 SettingItem 保存/测试按钮）；
  测试消息走既有 `send_test_msg` 链路——渠道开 + 凭据齐 + 收件人有绑定才
  真实投递（收件人无绑定即触发既有 debug 日志，排错口径不变）。

## 后果

- **明示不做**：群机器人 webhook（面向群而非个人，与用户级订阅模型不匹配，
  登记候选池）；钉钉新 v1.0「统一消息」接口（沿用 asyncsend_v2 成熟端点）；
  飞书卡片/富文本消息（统一文本消息，卡片需求登记候选池）；IM 渠道的送达
  回执对账（发送即认定成功，失败走日志与任务记录）。
- **降级矩阵**：渠道关 / 凭据缺 → 渠道不进发送映射；用户无绑定 → 单用户
  跳过；单渠道异常 → per-channel 隔离；均不影响其他渠道投递。
- **安全**：secret 值级加密 + 不回传；发送内容为既有消息文本（无新增敏感面）；
  凭据仅服务端可见，与 G2a 的 OAuth 凭据相互独立（登录与消息可用不同应用，
  权限范围各自最小化）。

## 测试与验收

- 单元（离线 stub http）：三家 token 获取与缓存（改密换 key）/ unionId→userid
  （钉钉，含缓存命中）/ 文本发送请求体与响应判定（errcode/code 包裹）/
  is_enable 降级（缺凭据）/ 账号解析按 flavor 归集（含未绑定跳过）；
- 集成：设置 API（secret 加密不回显 / 越权 403）；测试消息链路（渠道开 +
  绑定存在时真实走 SDK stub）；
- 门禁：pytest / ruff / 跨 app import / i18n po；前端 typecheck / eslint /
  locale-keys；全量 E2E 回归。
