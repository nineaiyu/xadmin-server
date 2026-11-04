# 部署与运维手册 · 开发栈与测试栈的网络纪律（dev-stack-network）

> 本文为《部署与运维手册》子页：开发 / 测试栈的**网络暴露面清单与自检命令**。
> 生产部署的安全基线见 [deployment-docker.md](deployment-docker.md) 与 [release-checklist.md](release-checklist.md)。

## 1. 为什么开发栈必须仅内网可达

开发 / 测试栈与生产形态的安全基线**刻意不同**（便于本地排障与联调），代价是它们
**不能出现在不可信网络上**：

- `config.yml`（git-ignored）含**真实密钥**（`SECRET_KEY` / `CAPTCHA_TICKET_SECRET` /
  三方应用凭据等；`config_example.yml` 只是模板）；
- `DEBUG: true` 时 Django 错误页会暴露源码片段 / 设置项 / 堆栈，响应头附带
  SQL 计数（`server/middleware.py`）；
- `XADMIN_APPS: [demo]` 时 demo CRUD 随站注册；验证码 / 登录锁定等防线可按需关闭；
- Vite 开发服务器（`pnpm dev` :8848）不做产物哈希 / CSP / 安全头等生产硬化，
  仅为本地即时生效设计；
- PG / Redis 无 TLS，仅密码认证；数据卷（`xadmin-postgresql/`、`xadmin-redis/`、
  `xadmin-db-backups*/`）是**本地活数据**，不设公网防线。

## 2. 端口与服务清单

### 2.1 开发栈（`docker-compose.yml`，`dev_up.sh` 起栈）

| 服务 / 端口 | 绑定形态 | 是否可出本机 | 纪律 |
|-------------|----------|--------------|------|
| nginx（dev 入口）`:8896` | `8896:8896`（compose 默认绑 **0.0.0.0**） | **默认所有网卡可达** | 仅限可信内网；单机开发建议改绑 `127.0.0.1:8896:8896`（见 §4） |
| server（gunicorn）`:8896` | 容器内，未单独发布 | 否 | 经 nginx 反代访问；**不要**直接发布 |
| PostgreSQL `:5432` / Redis `:6379` | 容器内，**未发布端口** | 否 | 保持不发布；不要加 `ports:` 映射 |
| Flower（任务监控）`:5566` | 容器内，经 nginx `/flower` 反代 | 否 | 走 nginx + 鉴权，不要直接发布 |
| 前端 Vite `:8848` | 本机进程（`pnpm dev`），默认 0.0.0.0 | 取决于启动参数 | 仅本地 / 可信内网使用；排障完关闭 |
| 测试栈 PG `127.0.0.1:55433`、Redis `127.0.0.1:56379`（`compose.test.yml`） | **仅回环发布** | 否 | 保持 `127.0.0.1` 前缀，不要去掉 |

### 2.2 生产形态对照

生产（installer / `docker-compose.prod.yml`）的安全基线由发布链路保证（HTTPS、
`SECURITY_HTTPS_ENABLED`、非 DEBUG、fail-fast 密钥校验等），**不要**把开发栈的
宽松假设带过去；反过来，开发栈也不要暴露到生产网络域之外。

## 3. 自检命令

```bash
# ① 列出当前所有监听端口与所属进程（macOS / Linux 通用）
docker ps --format '{{.Names}}\t{{.Ports}}'          # 容器端口映射面
lsof -nP -iTCP -sTCP:LISTEN | grep -E '8896|8848|55433|56379|5432|6379|5566'
# Linux 备选：ss -ltnp | grep -E '8896|8848|55433'

# ② 判定绑定面：期望 55433/56379 只见 127.0.0.1 前缀；8896 若为 *:8896 即全网卡
lsof -nP -iTCP:8896 -sTCP:LISTEN
lsof -nP -iTCP:55433 -sTCP:LISTEN                     # 应显示 127.0.0.1:55433

# ③ 从另一台（不可信网络内的）机器做外向探测：以下全部应 connection refused / timeout
nc -zv <主机IP> 8896
nc -zv <主机IP> 55433
nc -zv <主机IP> 56379
nc -zv <主机IP> 8848

# ④ 桥接网络内不应有到 PG/Redis 的发布端口（开发栈数据卷只在 compose 网络内）
docker compose ps                                      # PORTS 列应只有 nginx 的 8896
```

判定口径：

- `②③` 任一项命中「本机回环之外的地址在监听且外机可达」→ 先收紧绑定或防火墙，
  再继续开发；
- macOS 首次起栈弹出的「允许传入连接」对话框：单机开发选「拒绝」不影响本地访问
  （回环不受防火墙影响）；确需内网联调时再按最小范围放行并事后收回。

## 4. 收紧建议（单机开发）

```yaml
# docker-compose.override.yml（git-ignored，本地覆盖）：
services:
  nginx:
    ports:
      - "127.0.0.1:8896:8896"   # dev 入口只绑回环；需内网联调时临时改回并限时收回
```

- Vite：`pnpm dev --host 127.0.0.1`（默认即本机；避免 `--host 0.0.0.0` 常开）；
- 笔记本 / 台式机在办公网内即视为「内网」，公共 Wi-Fi / 不可信网络下**先停栈再联网**；
- 云上开发机（跳板 ECS 等）：安全组按「仅办公网出口 IP」放行 8896，其余端口一律
  不放行；`manage.py doctor` 的安全基线检查（CSRF / HTTPS / CSP）不覆盖网络面，
  需按本文 §3 单独自检。
