# 验证码风险分级运行手册（off / shadow / enforce）

> 适用：交互式验证码 + 风险分级（`captcha` 域）。
> 设计口径见 [验证码企业级增强方案](../plans/验证码企业级增强方案-2026.10.md) 与
> [实施细则](../plans/验证码企业级增强实施细则-2026.10.md)；
> 接口与挂载面见 [auth-flows.md](../architecture/auth-flows.md)。

## 一、三档模式与切换顺序

| 模式 | 行为 | 使用场景 |
|------|------|----------|
| `off`（默认） | 与历史行为一致：按场景出题 / 判题 / 发票，风险层不运行 | 升级初始态、事故回退 |
| `shadow` | 完整评分与决策并落日志，不改变放行结果 | 积累基线、校准阈值 |
| `enforce` | 按 `allow / light / challenge / hard / deny` 执行 | 影子期达标后启用 |

切换入口：管理端「系统设置 → 验证码 → 风险策略」，或
`PATCH /api/settings/captcha/risk`（字段 `CAPTCHA_RISK_MODE`）。也可在
`config.yml` 覆盖，但运行期以设置页热更为准。

**顺序**：`off → shadow`（建议 ≥3 个自然日、≥1000 次验证）→ `enforce`。
每次切换后观察「验证数据」页签的趋势、决策分布、风险分分布与 TopN，再决定下一步。

## 二、开启 enforce 前的硬检查

```bash
python manage.py doctor          # 关注「enforce 且信任网段为空」告警
python manage.py sync_menu_permissions --dry-run   # 权限点缺口 0
```

- **信任网段**：复用 `SECURITY_LOGIN_IP_WHITE_LIST`；`CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS`
  默认开启（RFC1918 / 回环无题直过），避免首日误拦办公网。关闭私网信任且白名单为空时
  `doctor` 会告警。
- **影子期基线**：`shadow` 下日志的 `would_decision` 应能回答「若开启会放行 / 拦下多少」；
  抽样人工核对被判 `deny` 的流量，真人比例应 < 0.5%。
- **PoW 预算**：`CAPTCHA_POW_IP_BUDGET`（默认 600/h）防止算力放大器；低配设备可通过
  `CAPTCHA_POW_BITS_LIGHT/HARD` 下调，但不要超过 `CAPTCHA_POW_BITS_MAX`（默认 20）。

## 二·补充：影子基线与压测脚本

```bash
# 合成流量（按画像轮换：干净 / headless / 非法时区 / 缺 env；默认 2.1s 间隔避开 30/min）
python loadtest/captcha_shadow_baseline.py --base-url http://127.0.0.1:8896 --rounds 50

# 独立环境 k6 压测（只压 challenge；429 是限流预期，5xx 必须为 0）
k6 run -e BASE_URL=https://example.com loadtest/captcha_k6.js

# 协议红队烟雾（旧路由 404 / 无签名 verify / 未知 challenge / 旧字段登录）
python loadtest/captcha_attack.py --base-url http://127.0.0.1:8896
```

影子期真实基线仍以 `CaptchaLog.would_decision` 与「验证数据」页签为准；脚本只用于
预发/合成流量，不替代真实流量观察。

## 三、观测与告警

- 页面：系统设置 → 验证码 → 验证数据（需要 `list:SecurityCaptchaStats` / `list:SecurityCaptchaLog`）。
- 关键指标：
  - **各题型通过率对比**：某题型通过率突然上升 = 该题型被打穿的第一个预警信号；
  - **风险分分布**：大量流量落入 `80-100` 说明规则或来源异常；
  - **决策分布 / 拦截率**：`deny` 占比持续升高先确认是否误拦；
  - **TopN**：拦截原因、可疑 IP 网段哈希、可疑指纹、可疑 UA 类。
- 告警：`CaptchaRiskAlertMessage` 接入既有通知渠道（站点消息），窗口 / 阈值由
  `CAPTCHA_ALERT_*` 控制；同窗口同类告警只发一次。

## 四、常见事故处置

| 现象 | 优先排查 | 处置 |
|------|----------|------|
| 真人被要求做难题 / 被拒 | 决策分布 + TopN 原因；是否 `net_proxy` / `net_datacenter` 网段误配；IP 网段漂移 | 临时切 `shadow`；把办公网加入 `SECURITY_LOGIN_IP_WHITE_LIST`；修正 CIDR 配置 |
| 登录后立刻要求重验 | 票据 IP 网段 / 环境指纹弱绑定漂移（移动网络、代理池、Canvas/WebGL 摘要变化） | 确认网络出口或浏览器环境是否变化；弱绑定不提供单独开关，优先稳定出口；必要时临时 `CAPTCHA_RISK_MODE=off` 并反馈环境差异 |
| 用户验证后提示票据无效 | 升级瞬间旧票据失效；服务端时间偏差；`CAPTCHA_TICKET_SECRET` 轮换未保留旧密钥 | 让用户重做一次；校时；轮换时把旧密钥写入 `CAPTCHA_TICKET_SECRET_PREVIOUS` |
| 音频验证码 404 / 无法播放 | 容器内 `flite` / `sox` 是否存在；`CAPTCHA_FLITE_PATH` / `CAPTCHA_SOX_PATH` | 镜像已内置 `/usr/bin/flite`、`/usr/bin/sox`；自建镜像需补装并校验 `Range` 返回 206 |
| Redis 故障 | 挑战 / 票据 / 计数全部 fail-closed，验证不可用 | 先恢复 Redis；这是预期安全语义，不要改为放行 |
| Celery 故障 | 验证仍可用，日志投递降级到进程内缓冲，可能延迟 / 丢弃 | 恢复 worker 后观察 `telemetry.fallback_pending`；日志缺失不影响验证结果 |
| PoW 长时间不完成 | 浏览器禁用 Worker / 无 `crypto.subtle` / 设备过慢 | 前端会降级主线程；仍不可用时下调 bits 或临时切 `shadow`；enforce 下无 `crypto.subtle` 的客户端会被 fail-closed，需 HTTPS + 现代浏览器或启用图片码回退 |
| 日志量过大 | `CAPTCHA_LOG_SUCCESS_SAMPLE_RATE`、保留期、窗口 | 下调成功采样率 / 保留天数；`captcha_purge_logs` 与每日 beat 任务会清理 |

## 五、回退

1. 设置页把 `CAPTCHA_RISK_MODE` 改为 `shadow`（保留观测、恢复历史体验）或 `off`（风险层完全停跑）。
2. `CAPTCHA_LOG_ENABLED=False` 可进一步停止写入（纯观测开关）。
3. 阶梯处置可置 `CAPTCHA_FAIL_STEP=0,0,0`（只记录不处置）。
4. 题型回退：从 `CAPTCHA_ENABLED_TYPES` 摘除对应题型即可（`image` 由回退开关单独控制）。

## 六、升级注意

- 已签发未消费的旧票据在升级后失效，用户需重做一次验证。
- 交互式 `verify` 在 `CAPTCHA_ACTION_BIND=True`（默认）时必须携带 `action`；前端需同批升级。
  旧第三方前端可临时 `CAPTCHA_ACTION_BIND=False` 过渡。
- 密钥单源是 `config.yml` 的 `CAPTCHA_TICKET_SECRET`；未配置时由 `SECRET_KEY` 派生并告警一次。
- 改完后端运行时代码需重启 `xadmin-server` 与 Celery 容器；改种子后先
  `load_init_json --check` 再复灌。

## 五、边界与未启用项

- **XFF 链路一致性信号**：仅在配置 `TRUSTED_PROXY_IPS` 时判定（直连不可信却带 XFF /
  可信链含非法地址 → 计入 `net_proxy`）；未配置时自动跳过，避免大面积误伤。
  机房/代理网段仍由 `CAPTCHA_RISK_DATACENTER_CIDRS` / `_PROXY_CIDRS` 覆盖。
- **处置计数未继承 `BlockUtilBase` 子类**：验证码维度是 `(维度, 值)`（IP 网段 / 动作 /
  指纹 / 账号哈希），与 `BlockUtil` 的 `(username, ip)` 键模板不匹配；`captcha/disposal.py`
  直接复用相同的 Redis 计数/冷却语义与 fail-safe 口径，未另造框架。
- **P50 口径**：大盘 `summary.latency_p50` 为校验期服务端 P50（最近 5000 条），
  allow 路径另有 <300ms 服务端用例；「无感路径端到端 P50」仍需结合 APM/前端 RUM。
- **零第三方外发**：验证码 E2E 监听登录 + PoW 全链路请求，断言目标均为同源；
  CSP `connect-src 'self'` 与 `pnpm test:e2e:csp` 双重守护。
- **`icon` 题型**：已提供程序化矢量图标（默认权重 0，按需启用）；ML 设备标识仍不做。
  `register` / `reset` 依赖上游发码端点的人机校验（覆盖门禁中显式豁免）。
