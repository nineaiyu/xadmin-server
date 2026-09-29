# 监控参考栈（Prometheus + Grafana + blackbox）

> 目标：把「HTTP 可用性 / API P95 / 任务成功率 / 队列积压」四项 SLO 从
> 「指标端点已暴露、无人值守看不到」推到**可看 + 可告警**。
> 相关：[observability.md](observability.md)（三支柱 / SLO 口径 / 演练记录）、
> [runbook.md](runbook.md)（故障处置）、`utils/monitoring/`（本栈资产）、
> `scripts/prometheus_alert_bridge.py`（告警投递桥接）。

## 一、形态与边界

| 项 | 说明 |
|---|---|
| 组成 | 独立编排 `utils/monitoring/docker-compose.monitoring.yml`：Prometheus（留存 30d）+ Grafana（预置面板）+ blackbox-exporter（健康探针） |
| 网络 | 接入主栈网络（默认 `xadmin-server_net`），直接抓 `server:8896`；主栈目录名不同用 `XADMIN_STACK_NETWORK` 覆盖 |
| 端口 | 默认只发布到 **回环**（`127.0.0.1:9090` / `127.0.0.1:3000`）；对外暴露须自行加认证与反代 |
| 数据 | `${MONITORING_DATA_DIR:-utils/monitoring-data}`（TSDB 与 Grafana 库，已 gitignore） |
| 不改变主栈 | 主栈的监控页趋势、db-backup 告警、OOM watcher、SLO 快照全部沿用；本栈是**只读抓取**的旁路 |
| 不替代 Alertmanager | 单机形态不引入 Alertmanager：告警投递走宿主侧桥接脚本（见 §四），组织已有 Alertmanager 也可直接接本 Prometheus |

## 二、启动

前置：主栈指标端点已启用（`config.yml` 的 `METRICS_ENABLED: true` 且 `METRICS_TOKEN` 非空，
两者缺一端点 404/403——见 [observability.md](observability.md) §七「指标端点启用」）。

```bash
cd <xadmin-server 仓库根>
cp utils/monitoring/metrics_token.example utils/monitoring/metrics_token
vi utils/monitoring/metrics_token        # 只写令牌本身（与 config.yml 的 METRICS_TOKEN 一致）
chmod 600 utils/monitoring/metrics_token

docker compose -f utils/monitoring/docker-compose.monitoring.yml up -d
docker compose -f utils/monitoring/docker-compose.monitoring.yml ps
```

验收：

```bash
# ① 目标健康（应为 1）——需要令牌，回环直连
curl -s -H "Authorization: Bearer $(cat utils/monitoring/metrics_token)" \
  http://127.0.0.1:9090/api/v1/query?query=up | head -c 400

# ② 抓到的自定义指标（四项 SLO 数据源）
curl -s -H "Authorization: Bearer $(cat utils/monitoring/metrics_token)" \
  'http://127.0.0.1:9090/api/v1/query?query=xadmin_celery_queue_length'
```

- Grafana：`http://127.0.0.1:3000`（`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`，默认 `admin/admin`，
  首次登录请改密）→ 目录 `xadmin` → 面板「xadmin 概览」。

## 三、指标与告警规则

指标清单见 [observability.md](observability.md) §三；本栈新增消费的是队列积压
（`xadmin_celery_queue_length`，broker 直读 LLEN，2026-09-29 起进端点）。

`utils/monitoring/alerts.yml` 的规则与 SLO 表同口径：

| 告警 | 条件 | 级别 |
|---|---|---|
| `XadminTargetDown` | 指标抓取失败 > 2m | critical |
| `XadminHealthProbeFailed` | healthz 探针非 2xx > 2m | critical |
| `XadminAvailabilityLow` | 5xx 率 > 1% 持续 5m | critical |
| `XadminApiLatencyHigh` | P95 > 1s 持续 10m | warning |
| `XadminTaskFailureRateHigh` | 近 24h 任务成功率 < 99% | warning |
| `XadminQueueBacklogHigh` / `Warning` | 队列积压 > 500 / > 100 | critical / warning |

授权池缓存键基数（观察项）规则默认注释，按部署规模开启并调参；键空间与收敛预案见
[../cache-keys-audit.md](../cache-keys-audit.md)。

**指标名漂移守护**：`tests/unit/common/test_monitoring_assets.py` 断言规则与面板表达式
只引用 `common/metrics.py` 中真实存在的指标——名字改了而规则没改会直接失败
（避免「看着有告警、其实永远不触发」）。

## 四、告警投递（桥接进站内信 / 邮件 / Webhook）

Prometheus 只做判定；投递由宿主侧 `scripts/prometheus_alert_bridge.py` 完成：

```
Prometheus 规则 → /api/v1/alerts（firing）
      ↓ 桥接脚本（每分钟一次，重复抑制默认 4h、恢复后立即重发）
POST /api/common/api/ops-alert（X-Ops-Token = 系统设置 OPS_ALERT_TOKEN）
      ↓
超管站内信 + 邮件 + 出站 Webhook system.ops_alert（与容器 OOM 告警同一条链路）
```

```bash
# 单次试跑（只打印应投递的告警，不投递、不落状态）
PROMETHEUS_URL=http://127.0.0.1:9090 OPS_ALERT_URL=https://<host>/api/common/api/ops-alert \
OPS_ALERT_TOKEN=<令牌> .venv/bin/python scripts/prometheus_alert_bridge.py --dry-run

# 真实投递（状态文件落在 PROMETHEUS_ALERT_STATE）
PROMETHEUS_URL=http://127.0.0.1:9090 OPS_ALERT_URL=https://<host>/api/common/api/ops-alert \
OPS_ALERT_TOKEN=<令牌> .venv/bin/python scripts/prometheus_alert_bridge.py
```

口径与边界：

- 同一告警（按 fingerprint）默认 **4h 最小重发间隔**（`PROMETHEUS_ALERT_REPEAT_SECONDS`）；
  告警恢复后记录即刻清除，**再次 firing 立即通知**；
- 投递失败**不推进**该告警时间戳（下一轮重试）；脚本非零退出，systemd/cron 侧可感知；
- 平台侧另有 60s 同来源同事件节流兜底（与 OOM 告警共享通道）；
- 桥接是宿主侧进程：主栈容器重启不影响它，Prometheus 不可达时只记错并等下一轮。

## 五、宿主侧 systemd 单元（三个 agent）

`utils/monitoring/systemd/` 提供可直接安装的单元样例（环境文件集中在 `/etc/xadmin/ops-alert.env`）：

| 单元 | 作用 | 调度 |
|---|---|---|
| `xadmin-oom-alert.service` | 容器 OOM 事件 → ops-alert（`utils/oom_alert.sh`） | 常驻（`Restart=always`） |
| `xadmin-slo-snapshot.{service,timer}` | 每日 SLO 快照 → JSONL（`utils/slo_snapshot_cron.sh`） | 06:17（`Persistent=true` 补跑） |
| `xadmin-prometheus-alert-bridge.{service,timer}` | Prometheus firing 告警 → ops-alert | 每 1 分钟（`OnBootSec=2min`） |

```bash
install -m 600 utils/monitoring/systemd/ops-alert.env.example /etc/xadmin/ops-alert.env
vi /etc/xadmin/ops-alert.env                    # 填令牌与端点
install -m 644 utils/monitoring/systemd/xadmin-*.service utils/monitoring/systemd/xadmin-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xadmin-oom-alert.service xadmin-slo-snapshot.timer xadmin-prometheus-alert-bridge.timer
systemctl list-timers 'xadmin-*'
```

- 单元里的 `WorkingDirectory` 默认 `/opt/xadmin/xadmin-server`（安装器形态路径），
  按实际部署目录调整；`User` 未声明（默认 root）——OOM watcher 需要 docker socket 权限；
- 无 systemd 的环境用 cron 等价代替：

```cron
*/1 * * * * . /etc/xadmin/ops-alert.env; cd /opt/xadmin/xadmin-server && .venv/bin/python scripts/prometheus_alert_bridge.py >> /var/log/xadmin-prom-alert.log 2>&1
17 6 * * *  . /etc/xadmin/ops-alert.env; cd /opt/xadmin/xadmin-server && bash utils/slo_snapshot_cron.sh >> /var/log/xadmin-slo.log 2>&1
```

## 六、镜像与季度核对

参考编排的镜像默认 `latest`（`PROMETHEUS_IMAGE` / `GRAFANA_IMAGE` / `BLACKBOX_IMAGE` 可覆盖）：

- 生产/离线环境请改为私有仓库地址并 **pin 具体版本**；
- 与 `xadmin-installer` 的离线镜像核对同一节奏执行（季度）：`docker compose ... config` 复核镜像串 →
  拉取新版本 → 起栈验收 §二 的两条验收命令 → 记录到发布窗口执行记录。
