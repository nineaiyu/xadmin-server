"""Django settings：Redis 缓存与 Channels 通道层（自 base.py 按域拆出）。

经 base.py 的 star-import 并入 settings 命名空间；本模块只依赖 CONFIG，
不反向依赖 base，保持 base → base 子模块的单向装配顺序。
"""

from ..const import CONFIG

# Redis 配置
REDIS_HOST = CONFIG.REDIS_HOST
REDIS_PORT = CONFIG.REDIS_PORT
REDIS_PASSWORD = CONFIG.REDIS_PASSWORD

DEFAULT_CACHE_ID = CONFIG.DEFAULT_CACHE_ID
CHANNEL_LAYERS_CACHE_ID = CONFIG.CHANNEL_LAYERS_CACHE_ID
CELERY_BROKER_CACHE_ID = CONFIG.CELERY_BROKER_CACHE_ID
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"redis://{REDIS_HOST}:{REDIS_PORT}/{DEFAULT_CACHE_ID}",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # 故障演练 2029-10 复测修复：Redis 冻结（容器 stop，连接挂起而非拒绝）时
            # socket 无超时会让请求挂在中间件/配置读取阶段（实测 10s+ 被 worker
            # timeout 打断、health 端点被拖挂）。局域网 redis 操作 <5ms，1s 超时充裕，
            # 冻结时快速失败（配合 ConfigCache 回落读库，见 packages/xadmin-common/common/core/config.py）。
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 8000,
                # 连接超时 0.2s：局域网建连正常 <1ms；冻结（连接挂起）时每个调用
                # 0.2s 快速失败（实测 1s 时多个串行调用累积到 10s，0.2s 收敛到秒级）
                "socket_connect_timeout": 0.2,
                # 读写超时 0.5s：正常操作 <10ms（大 pattern SCAN 留余量），仅兜底
                "socket_timeout": 0.5,
                "retry_on_timeout": False,
            },
            "PASSWORD": REDIS_PASSWORD,
            "DECODE_RESPONSES": True,
            "REDIS_CLIENT_KWARGS": {"health_check_interval": 30},
            # 故障演练 2029-10 复测（第二轮）：Redis 冻结时 TimeoutError 被
            # django_redis 转换为 ConnectionInterrupted，但默认继续向上抛——
            # DRF 限流器等框架层调用点不兜异常 → 请求 500（实测 health 返回
            # 500）。开启 IGNORE_EXCEPTIONS：读返回 None、写静默失败（标准降级
            # 语义：缓存不可用时 fail-open，业务回落数据源/重算）。
            "IGNORE_EXCEPTIONS": True,
        },
        "TIMEOUT": 60 * 15,
        "KEY_FUNCTION": "common.base.utils.redis_key_func",
        "REVERSE_KEY_FUNCTION": "common.base.utils.redis_reverse_key_func",
    },
}

# websocket 消息需要用到redis的消息发布订阅
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "common.cache.channel.RedisChannelLayer",
        # "BACKEND": "channels_redis.pubsub.RedisPubSubChannelLayer",
        "CONFIG": {
            # 注意：这里必须用 dict 形式的 host，channels_redis 会把额外 kwargs
            # 透传给 redis-py ConnectionPool。redis-py 8.x 起默认 socket_timeout
            # 从 None 改为 5s，而 channels_redis 的 receive() 使用 BZPOPMIN
            # 服务端阻塞 5s 长轮询，两个 5s 竞速会导致偶发
            # "Timeout reading from redis" 并杀死整个 websocket consumer。
            # 显式关闭 socket_timeout 以恢复无限阻塞等待。
            "hosts": [
                {
                    "address": f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{CHANNEL_LAYERS_CACHE_ID}",
                    "socket_timeout": None,
                }
            ],
        },
    },
}
