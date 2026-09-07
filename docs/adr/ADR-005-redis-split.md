# ADR-005: Redis 单实例三用途共用的现状维持与拆分预案

- 状态：已接受（2026-09-06，PERF-4）
- 关联：优化升级开发文档 PERF-4

## 背景

当前单实例 Redis 以 db 编号承载三个用途（`server/settings/base.py`）：

| 用途 | db | 关键配置 |
|------|----|----------|
| Django 缓存（权限/元数据/SysConfig） | `DEFAULT_CACHE_ID`（默认 1） | `CACHES["default"]` |
| Channels channel layer（WS 推送/在线心跳） | `CHANNEL_LAYERS_CACHE_ID`（默认 2） | `CHANNEL_LAYERS` |
| Celery broker | `CELERY_BROKER_CACHE_ID`（默认 3） | `CELERY_BROKER_URL` |

三者共用同一实例，故障域共享：broker 阻塞或大 key 慢查询会同时波及缓存命中与 WS 推送。

## 决策

**现状维持单实例 + 分 db**，同时固化如下拆分触发条件（满足任一即启动拆分）：

1. WebSocket 并发连接持续 > 1000，或 channel layer 出现积压告警；
2. Celery 队列常态化积压（> 1000 待消费）或 broker CPU 持续 > 60%；
3. 缓存命中率因 Redis 负载下降，权限缓存 TTL 兜底被频繁穿透。

## 拆分顺序（触发后执行）

1. **优先拆 Channels**：channel layer 的 pubsub 与心跳写放大最明显，独立实例后两侧行为均不受对方影响；仅需改 `CHANNEL_LAYERS` 的 hosts 指向新实例；
2. **再拆 Celery broker**：broker 迁移需滚动重启 worker，选择低峰窗口；改 `CELERY_BROKER_URL`；
3. **缓存实例最后**：迁移只需切换 `CACHES["default"]`，配合 `expire_caches` 管理命令预热。

## 不做的替代方案

- 不引入 Redis Cluster / Sentinel：单机部署形态下运维成本大于收益，上述三分实例已足够隔离故障域；
- 不改用 channels 的 InMemory/其他后端：Redis pubsub 已满足当前规模。

## 后果

- 正面：现状零改动，故障隔离路径清晰，触发条件可量化（接入 DEP-3 指标后可直接观测）；
- 负面：触发前 broker 与缓存仍共享故障域（可接受：单机部署下 PG 同样是单点，Redis 拆分不改变整体可用性量级）。
