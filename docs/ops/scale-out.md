# 横向扩展 runbook（单机多副本 → 多机）

> 目标：把 web（`server`）与任务（`celery-worker` / `celery-heavy`）扩到多副本，
> 且**不给既有单副本形态引入任何行为变化**。
> 相关：[deployment.md](deployment.md) §6.1（升级与迁移）、`docker-compose.scale.yml`、
> `ops/xadmin-backend.multi.conf`、[storage.md](storage.md)（媒体共享/S3 后端）。

## 一、三条硬约束（不满足就会出问题）

| # | 约束 | 不满足的后果 | 落地方式 |
|---|---|---|---|
| 1 | **迁移只执行一次** | 两个副本同时 `migrate` → DDL 互锁 / 半迁移状态 | 一次性 `migrate` 服务先执行 + web 侧 `AUTO_MIGRATE=false`（`docker-compose.prod.yml` / `scale.yml`） |
| 2 | **beat 保持单副本** | 周期任务、定时报表被重复投递（`django_celery_beat` 不做跨进程互斥） | base compose 已声明 `deploy.replicas: 1`；扩展时只扩 `server` / `celery-worker` / `celery-heavy` |
| 3 | **nginx 后端必须轮询全部副本** | 变量式 `proxy_pass` 只解析到一个地址 → 流量全落一台，扩容无收益 | `docker-compose.scale.yml` 覆盖挂载 `ops/xadmin-backend.multi.conf`（`upstream + zone + resolve`） |

> WS 层无需额外配置：`RedisChannelLayer` 已就绪，跨副本广播走 Redis；HTTP/WS 请求
> 本身无状态（JWT/RBAC/会话/缓存都在 Redis 与 DB），不要求会话粘性。

## 二、扩展步骤（单机多副本）

前置：生产 overlay 已可用（`.env` 凭据齐全、宿主 `./data` 属主 1001，见 deployment.md §3）。

```bash
# 0. 变量：后续命令共用同一组 -f（也可写入 shell 变量 / COMPOSE_FILE 环境变量）
FILES="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.scale.yml"

# 1. 构建镜像（scale overlay 复用 server 服务的镜像 tag）
docker compose $FILES build server

# 2. 先迁移（一次性服务；失败即止，不要带病起副本）
docker compose $FILES run --rm migrate

# 3. 起副本（server=3 / worker=2 为例；beat 不参与扩容）
docker compose $FILES up -d --scale server=3 --scale celery-worker=2
```

### 验证清单

```bash
# a. 副本数（server 应有 3 个容器）
docker compose $FILES ps --format 'table {{.Service}}\t{{.Name}}\t{{.Status}}'

# b. 各副本健康（healthz 全 true）
for c in $(docker compose $FILES ps -q server); do
  docker exec "$c" curl -fsL http://localhost:8896/api/common/api/health >/dev/null && echo "$c ok"
done

# c. nginx 上游确实轮询到多台：连续访问后观察 "$upstream_addr" 列出现不同 IP
docker exec xadmin-nginx tail -n 20 /var/log/nginx/tcp-access.log

# d. 外部入口（8896 为 L4 流转发）
curl -fsS http://127.0.0.1:8896/api/common/api/health >/dev/null && echo "edge ok"

# e. 任务面：worker 副本在消费
docker exec "$(docker compose $FILES ps -q celery-worker | head -n1)" celery -A server inspect ping
```

### 扩缩容

```bash
# 扩容：再执行一次 up -d --scale（已运行副本不受影响）
docker compose $FILES up -d --scale server=5

# 缩容：缩容前确认没有长连接依赖（WS 客户端会自动重连到其它副本）
docker compose $FILES up -d --scale server=2
```

副本 IP 变化由 nginx `resolver ... valid=10s` + `resolve` 自动跟随，**不需要**
reload/restart nginx。

## 三、限制与边界

- **单机形态**：PostgreSQL / Redis / nginx / 备份服务仍是单点，本 overlay 解决的是
  「应用层可横向扩展」，不解决高可用与故障切换（PG/Redis 需另行做副本与切换方案）；
- **跨主机扩展**：需要共享 DB/Redis 与媒体目录（NFS 或 [storage.md](storage.md) 的
  S3 后端），并自备外部 L4/L7 负载均衡；本仓自带的 `ops/nginx.conf` 只负责同宿主
  副本的轮询；
- **媒体目录**：多副本必须共享同一份上传目录（同宿主 bind mount 天然共享），否则
  上传落在后端 A、下载命中后端 B 会 404；
- **beat 不扩容**：重复 beat 会重复投递周期任务；如需 beat 高可用，应使用带分布式
  锁的调度方案（未实现，重开条件见本节末）；
- **`migrate` 只跑一次**：滚动发布时每条流水线只跑一次；若多副本下误开
  `AUTO_MIGRATE=true`，两个 web 会并发迁移（DDL 互锁风险）；
- **nginx multi 形态的启动要求**：`upstream ... resolve` 在 nginx 启动时需要
  `server` 可解析，编排首次 `up` 时 nginx 可能失败一次并由 `restart: always` 重试
  到 DNS 出现（数十秒收敛）；稳态与单副本形态无差异。

> 只是发布期间不想断连（不追求多副本）时，不必扩副本：直接用
> [blue-green.md](blue-green.md) 的叠加滚动发布（同样依赖本 overlay 的 nginx 多后端与迁移前置）。

## 四、回退到单副本

1. 先缩容：`docker compose $FILES up -d --scale server=1`；
2. 换回单副本形态：去掉 `-f docker-compose.scale.yml` 后 `up -d`——nginx 挂回
   `xadmin-backend.single.conf`（变量式），web 侧恢复 `AUTO_MIGRATE` 默认值（true，
   启动自动迁移）；
3. 复核 healthz 与 nginx 访问日志（`$upstream_addr` 只剩一台）。

## 五、扩展后观察项

- `xadmin_http_request_duration_seconds` 各副本应接近（差异大说明轮询不均或某副本受限）；
- `/api/common/api/metrics` 的 `xadmin_authz_grants_cache_keys`（授权池键基数）随副本数
  与并发菜单数增长，TTL 300s 兜底（见 [../cache-keys-audit.md](../cache-keys-audit.md) 观察项）；
- 任务队列长度与 worker 并发（`celery inspect`）；重队列（heavy）按任务耗时单独扩容。
