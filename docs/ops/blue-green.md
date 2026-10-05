# 零停机发布 runbook（蓝绿 / 叠加滚动）

> 适用：单机 compose 生产形态（`docker-compose.yml` + `docker-compose.prod.yml` + `docker-compose.scale.yml`）。
> 目标：发布期间**新连接始终有健康副本承接**，旧副本优雅退出并跑完在途请求。
> 相关：[scale-out.md](scale-out.md)（多副本与 nginx 多后端）、[deployment.md](deployment.md) §6（升级与回滚）、
> [release-checklist.md](release-checklist.md) §0（发布窗口公告）。
>
> 前置能力（缺一不可）：
> ① 一次性 `migrate` 服务（迁移不随 web 启动，见 deployment.md §6.1）；
> ② nginx 多后端 `upstream + zone + resolve`（`ops/xadmin-backend.multi.conf`，`docker-compose.scale.yml` 挂载）；
> ③ gunicorn `--graceful-timeout 30` + 服务 `stop_grace_period: 40s`（SIGTERM 后等在途请求跑完）；
> ④ healthcheck `start_period: 90s`（启动期不误判 unhealthy）。

## 一、原理

- nginx（stream/L4）按 DNS 名 `server` 轮询：`resolver 127.0.0.11 valid=10s` + `server server:8896 resolve`；
- Docker 内嵌 DNS 对同一网络里**所有**带该别名的容器（compose 服务的各副本 + 手工起的容器）返回多条 A 记录；
- 因此把新版本容器「以同一别名加入网络」，nginx 在 `valid=10s` 内把新连接分摊过去；
- 发布 = **加新 → 等健康 → 逐个摘旧**。任一时刻至少有一批健康副本在别名下，没有真空期。

> 与「先停后起」的单副本滚动重建相比：后者在重启 3–10 秒内必然断连（L4 无粘性也无法重放请求），
> 本文方案把断连窗口压缩到「单个副本的优雅退出期」，且连接级失败会由 stream 自动转下一台。

## 二、发布步骤

### 0. 变量与前置检查

```bash
FILES="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.scale.yml"
NEW=3                                             # 与线上副本数一致
NET=$(docker network ls --format '{{.Name}}' | grep '_net$' | head -1)   # compose 项目网络
BLUE=$(docker compose $FILES ps -q server | head -1)                     # 任取一个在跑的旧副本
docker image inspect -f '{{.Id}}' xadmin-server   # 记录旧 image id（回滚用，见 §四）
```

迁移兼容性（**最重要的一条**）：新旧代码会同时跑在同一个库上，因此本窗口的迁移必须是
「扩展型」——只加表/加列（可空或带默认值），**不得**删除/重命名旧代码仍在用的列；
破坏性变更拆到下一个窗口（先切代码、再删列）。

### 1. 备份 + 迁移

```bash
docker compose $FILES run --rm migrate
```

### 2. 构建新镜像

```bash
docker compose $FILES build server
```

> 镜像 tag 名不变（`xadmin-server`）：已在运行的旧容器引用的是旧 image id，不受 tag 移动影响。

### 3. 以同一网络别名起新版本副本（绿色）

```bash
# 环境变量与挂载直接沿用旧容器（避免手抄 DB_PASSWORD / 数据目录导致漂移）
ENV_FILE=$(mktemp)
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BLUE" > "$ENV_FILE"
VOLUMES=$(docker inspect -f '{{range .Mounts}}-v {{.Source}}:{{.Destination}} {{end}}' "$BLUE")

for i in $(seq 1 "$NEW"); do
  docker run -d --name "xadmin-next-$i" --hostname "xadmin-next-$i" \
    --network "$NET" --network-alias server --restart no \
    --env-file "$ENV_FILE" -e AUTO_MIGRATE=false \
    $VOLUMES xadmin-server start server
done
```

### 4. 等绿色副本健康

```bash
for i in $(seq 1 "$NEW"); do
  until docker exec "xadmin-next-$i" curl -fsL http://localhost:8896/api/common/api/health > /dev/null; do
    sleep 3
  done
  echo "xadmin-next-$i healthy"
done
curl -fsS http://127.0.0.1:8896/api/common/api/health && echo "edge ok"
```

### 5. 逐个摘除旧副本（优雅退出）

```bash
# 一次摘一个 + 间隔 10s：等 DNS 与 nginx resolver 缓存（valid=10s）刷新
for c in $(docker compose $FILES ps -q server); do
  docker stop -t 40 "$c"        # gunicorn 收 SIGTERM：关闭监听 → 等在途请求 → 退出
  sleep 10
done
```

### 6. 回到 compose 管理（新镜像的正式副本）

```bash
docker compose $FILES up -d --scale server="$NEW"          # 重建为新镜像
docker rm -f $(docker ps -aq -f 'name=^xadmin-next-')
docker compose $FILES ps --format 'table {{.Service}}\t{{.Name}}\t{{.Status}}'
```

### 7. 任务侧（worker / heavy / beat）

```bash
# worker/heavy：compose 检测镜像变化即重建；SIGTERM 走 warm shutdown
# （stop_grace_period 已放宽：worker 120s / heavy 300s，长任务用 stop -t 覆盖）
docker compose $FILES up -d --scale celery-worker=2 --scale celery-heavy=1

# beat：单例，先停后起；调度空窗秒级（max-interval 60，不会漏跨窗口的周期任务）
docker compose $FILES up -d celery-beat
```

## 三、验收与观察

```bash
# a. healthz
curl -fsS http://127.0.0.1:8896/api/common/api/health
# b. nginx 上游只剩新副本（$upstream_addr 列）
docker exec xadmin-nginx tail -n 30 /var/log/nginx/tcp-access.log
# c. 无残留旧容器/绿色容器
docker ps --format '{{.Names}}\t{{.Image}}' | grep -E 'xadmin-server|server-'
# d. 30 分钟观察（同发布 checklist §0：错误率 / 队列积压 / unexpected_exception.log）
```

## 四、回滚

1. **代码回滚**：用同一套叠加滚动流程起「旧 image」的副本，再摘掉新副本：

   ```bash
   # §二.0 已记录 OLD_ID（发布前的 xadmin-server image id）
   docker tag "$OLD_ID" xadmin-server:rollback
   # 把 §二.3 的 `xadmin-server start server` 换成 `xadmin-server:rollback start server`，
   # 依次执行 §二.4 → §二.5 → §二.6（新副本换成 compose 管理的旧版本副本）
   ```

   更快的一步式止损：`docker compose $FILES stop server && docker compose $FILES up -d --scale server="$NEW"`
   —— 前提是 tag `xadmin-server` 已被打回旧 image id。

2. **数据回滚**：Django 迁移不做反向回滚，按 [deployment.md](deployment.md) §6.2 走备份恢复（清空重建）；
   因此「扩展型迁移 + 分两次发布」是零停机方案的硬约束，不是可选项。

## 五、限制与边界

- 仍是**单机**形态：PG/Redis/nginx 为单点，本方案只保证「应用层发布不断连」，不做宿主机故障切换；
- 绿色副本是**手工容器**（不在 compose 管理内）：发布结束后必须清理，否则它们会一直占用别名接流量，
  且不受 compose 的重建/回滚管理；
- **离线安装器**（xadmin-installer）路径仍是维护窗口升级：`7_upgrade.sh` 会停服迁移，
  按 [release-checklist.md](release-checklist.md) §0 提前发布窗口公告；
- 跨主机扩展、共享媒体目录与外部负载均衡见 [scale-out.md](scale-out.md) §三。
