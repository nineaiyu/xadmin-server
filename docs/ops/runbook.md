# 运维 Runbook：常见故障 → 处置步骤

> 半年规划 T6.2 交付物（2026-09-06）。按「现象 → 定位 → 处置」组织，持续追加。
> 通用前置：先看 `GET /api/common/api/health`（免认证）与 `data/logs/server.log`（按 `X-Request-Id` 检索），
> Docker 部署用 `docker compose ps` + `docker logs <container>` 替代进程检查。

## 1. 服务启动即退出

- **定位**：`data/logs/` 下启动日志；常见三类——`config.yml` 不存在 / `SECRET_KEY` 为空且 DEBUG=false / 端口被占用（exit code 10）。
- **处置**：`cp config_example.yml config.yml` 并填 `SECRET_KEY`（`cat /dev/urandom | tr -dc A-Za-z0-9 | head -c 49`）；端口占用改 `HTTP_LISTEN_PORT` 或释放端口。

## 2. healthz `db_status: false`

- **定位**：`config.yml` 的 `DB_HOST/PORT/USER/PASSWORD`；容器网络是否互通；数据库自身日志。
- **处置**：修复连接配置或网络后重启 server；若为连接数耗尽（PG `max_connections`），先重启打满的 gunicorn，再调大上限或接入 PgBouncer。

## 3. healthz `redis_status: false`

- **定位**：Redis 进程、`REDIS_PASSWORD` 是否与 `--requirepass` 一致、内存是否打满（`redis-cli info memory`）。
- **处置**：修复后重启；Redis 数据可丢（仅缓存/broker），确认 celery 队列未积压再清空（`flushdb` 前先 `docker compose stop celery-*`，防止任务丢失）。

## 4. healthz `celery_status: false`（异步任务不可用）

- **定位**：`docker compose ps` 看 celery-worker/celery-heavy 是否 Up；心跳文件是否存在（`utils/check_celery.sh [celery|heavy]`）；worker 日志尾部。
- **处置**：worker 崩溃 → 重启对应容器；反复崩溃多为 broker 断连或任务代码异常（看 `unexpected_exception.log`）。注意 API 正常但导入/导出/通知将滞留队列，恢复 worker 后自动消费。

## 5. 登录提示「当前服务器不允许登录」/ 频繁 429

- **定位**：登录限流默认 `login: 50/h`（GET/POST 共享额度），共享出口（办公网/爬虫）易打满。
- **处置**：临时等窗口重置；长期在 `config.yml` 的 `DEFAULT_THROTTLE_RATES` 调高 `login`，或给反向代理传递真实 IP（`X-Forwarded-For`），避免全公司共享一个限流桶。

## 6. 用户被登录锁定（提示锁定）

- **定位**：`SECURITY_LOGIN_LIMIT_COUNT`（默认 7 次）/ `SECURITY_LOGIN_LIMIT_TIME`（默认 30 分钟）。
- **处置**：等待自动解锁；管理员可改用户密码走正常解锁路径。频繁触发时结合操作日志确认是否撞库，必要时封禁来源 IP（`SECURITY_LOGIN_IP_BLACK_LIST`）。

## 7. WebSocket 连不上（通知/聊天无实时推送）

- **定位**：浏览器控制台 ws 报错；nginx 是否配置 `/ws/` 升级头（`Upgrade`/`Connection`）；Daphne 是否随 web 容器启动。
- **处置**：补 nginx 配置：
  ```nginx
  location /ws/ {
      proxy_pass http://server:8896;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      proxy_read_timeout 300s;
  }
  ```

## 8. 导入/导出无响应或任务积压

- **定位**：healthz 的 `celery_status`（heavy 队列）；Flower 面板 heavy 队列长度；`start celery_heavy` 容器日志。
- **处置**：heavy worker 不在线则重启；任务积压时确认数据量是否超过 `EXPORT_MAX_LIMIT`（默认 20000，导出超限直接拒绝）；批量导入无 worker 时会自动降级同步执行，响应慢属正常。

## 9. 权限/菜单改了不生效

- **定位**：`system/signal_handler.py` 信号失效链路是否被绕过——绕过 API/ORM 信号直改缓存关联数据（如 SQL 手工 UPDATE）不会触发失效。
- **处置**：常规情况重新登录或等待 TTL；立即生效可执行 `python manage.py expire_caches system`（或重启 worker 触发 `django_ready` 清响应缓存）。持续不生效按 [architecture/cache.md](../architecture/cache.md) 的调试关键字核对缓存键。

## 10. Flower 无法访问或 401

- **定位**：flower 随 web 容器启动（`start web`/`start gunicorn`）；T5.3 收尾后 `CELERY_FLOWER_AUTH` 未配置时仅允许绑定 127.0.0.1，显式绑定其他地址会拒绝启动（exit 11）。
- **处置**：生产环境在 `config.yml` 配置 `CELERY_FLOWER_AUTH: flower:<强密码>`（格式 用户:密码）后重启 web；管理台经 `/api/flower/` 代理访问，代理自动携带认证。

## 11. 磁盘占满

- **定位**：`du -sh data/logs data/uploads xadmin-db-backups` 三大目录；Docker 卷 `df -h`。
- **处置**：日志按天轮转可直接删除历史 `.log.*`；备份滚动保留 7 天（`KEEP_DAYS`），可手工清旧；媒体文件属业务数据，只能走归档不能直删。

## 12. 误删数据恢复 / 备份演练

- **定位**：确认最近一份完好的 `xadmin-db-backups/<库名>_<时间戳>.sql.gz`。
- **处置**：`sh utils/db_restore.sh <备份文件> xadmin`（**清空重建**目标库，先停写入并确认）；媒体目录（uploads）不在 pg_dump 范围，需另行同步/恢复；演练后记录 RTO（目标 ≤30 分钟）。

## 13. migrate 卡住或失败

- **定位**：PG 锁等待（`select * from pg_stat_activity where wait_event is not null`）；迁移依赖（如扩展未装）。
- **处置**：杀掉挂起的 DDL 会话后重试；多实例同时 migrate 会互相锁，升级时保证只有一个实例执行迁移（先扩容 server 前的迁移步骤单独跑）。

## 14. 上传图片失败（1002/1003）

- **定位**：code=1002 类型不在白名单（png/jpeg/jpg/gif）、1003 超过大小上限（站点配置 `PICTURE_UPLOAD_SIZE`）。
- **处置**：按业务需要扩白名单或调大上限（设置中心 → 基本配置）；注意反向代理 `client_max_body_size` 需同步调大。

## 15. pip-audit 周检告警（CVE 响应）

- **定位**：Security Audit workflow 邮件/页面，确认漏洞包、版本、严重级别、是否有 fix 版本。
- **处置**：有 fix → 升级 patch 版本走独立 PR + 全量门禁；无 fix → 评估缓解（配置收紧/下线特性）并在 `docs/security-review.md` 登记豁免理由与复查日期。
