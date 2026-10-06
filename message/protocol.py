#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""WebSocket 消息协议 Schema。

上行/下行帧均为 JSON，公共形状：

    {
        "action": "<Action>",   # 动作，见 MessageAction
        "data": ...,            # 载荷，由具体 action 决定（见各 Payload TypedDict）
        "mid": "<str>",         # 可选，客户端回显一致性标记
        "v": 1,                 # 协议版本，可选；出站帧由服务端统一携带
    }

出站帧（send_base_json）额外含 code / detail / timestamp。
新增 action 必须：① 在此登记枚举；② 补充对应 Payload TypedDict；
③ 在 message/ws_schema.py 登记 payload definition 并重跑
`python scripts/gen_ws_frame_schema.py`（docs/schema/ws-frame.schema.json 是生成物，
由本模块枚举与 TypedDict 单源导出，禁止手改）。
"""

from enum import StrEnum
from typing import Any, TypedDict

PROTOCOL_VERSION = 1


class MessageAction(StrEnum):
    """消息动作枚举（字符串值，方便 match/比较与载荷路由）。

    `chat_message` 在两条通道上语义不同（历史原因，见各 Payload 文档）：
    - `ws/message/<group>/<username>`（MessageNotify，历史通道，仅兼容保留）载荷 = ChatMessagePayload；
    - `ws/chat/`（ChatNotify，聊天室重构后的通道）载荷 = ChatRoomMessagePayload。
    """

    PING = "ping"  # 心跳：上行 ping → 下行 data='pong'
    USERINFO = "userinfo"  # 请求/推送当前登录用户信息
    PUSH_MESSAGE = "push_message"  # 站内信/通知推送
    CHAT_MESSAGE = "chat_message"  # 聊天室消息（双向）
    CHAT_RECALL = "chat_recall"  # 消息撤回（下行广播，ws/chat/；上行撤回走 REST recall 端点）
    CHAT_REACTION = "chat_reaction"  # 消息表情回应（双向，ws/chat/）
    CHAT_READ = "chat_read"  # 已读回执（上行 chat_read → 下行游标）
    CHAT_UNREAD = "chat_unread"  # 未读红点推送（下行，ws/chat/）
    TASK_LOG = "task_log"  # 任务执行日志增量推送（system/ws.py）
    MONITOR = "monitor"  # 监控面板指标推送（system/ws_monitor.py）
    SCREEN_COMMAND = "screen_command"  # 大屏远程控制指令（dataset/ws_screen.py，下行单向）
    SCREEN_DATA = "screen_data"  # 大屏服务端聚合数据推送（dataset/ws_screen.py，下行单向）
    SCREEN_PAGE_STATE = "screen_page_state"  # 大屏展示端当前页上报（dataset/ws_screen.py，上行单向）


class InboundMessage(TypedDict, total=False):
    """客户端→服务端帧。"""

    action: str
    data: dict[str, Any]
    mid: str
    v: int


class OutboundMessage(TypedDict, total=False):
    """服务端→客户端帧。"""

    code: int
    detail: str
    action: str
    timestamp: str
    data: Any
    mid: str
    v: int


class PingPayload(TypedDict):
    content: str


class UserinfoPayload(TypedDict):
    pk: str
    userinfo: dict[str, Any]


class ChatMessagePayload(TypedDict, total=False):
    """历史聊天通道（ws/message/*）的气泡载荷；服务端回填 pk/username 后广播。

    仅供旧 MessageNotify 兼容使用，新聊天室请用 ChatRoomMessagePayload。
    """

    text: str
    pk: str
    username: str
    timestamp: str


class ChatRoomMessagePayload(TypedDict, total=False):
    """聊天室消息载荷（ws/chat/ 通道）：落库后广播的完整消息记录。

    id 为自增主键（即游标），client_msg_id 供发送端做本地幂等对齐。
    message_type 取值 text / ai / system / image / video / audio / file；附件消息
    （image / video / audio / file）的上行帧额外携带 `file_pk`（先经上传端点取得），
    下行载荷的 extra 内附 ChatAttachmentPayload（含受鉴权取件 url）。
    """

    id: int
    room_id: int
    room_type: str
    sender_pk: int | None
    sender_name: str
    sender_avatar: str
    message_type: str
    content: str
    created_time: str
    client_msg_id: str
    extra: dict[str, Any]


class ChatAttachmentPayload(TypedDict, total=False):
    """附件渲染信息（附件消息的 ``extra["file"]``）。

    - ``url``：受鉴权取件地址（图片 img / 文件下载共用；撤回或附件被清理后为空）；
    - ``missing``：附件记录已失效（外键 SET_NULL / 消息已撤回），前端渲染占位提示。
    """

    pk: str
    filename: str
    filesize: int
    mime_type: str
    category: str
    kind: str
    url: str
    missing: bool


class ChatRecallPayload(TypedDict, total=False):
    """消息撤回广播帧（下行）：REST 撤回端点落库后向房间下发，多端同步对齐。"""

    message_id: int
    id: int
    room_id: int
    operator_pk: int


class ChatReactionPayload(TypedDict, total=False):
    """表情回应帧（上行）：对某条消息添加 / 移除自己的 emoji 回应。

    - ``message``：目标消息 pk（ChatMessage 自增主键）；
    - ``emoji``：回应表情，去首尾空白后 1-16 字符；
    - ``op``：add 添加 / remove 移除。add 幂等（重复 add 不重复记录）；
      remove 只能移除自己的回应（载荷不含目标用户字段，无法替他人移除）。

    消息不存在 / 已撤回 / 操作者非房间成员 / 机器消息（ai / system）时服务端
    静默忽略（不回执也不广播）；emoji 超长或回应数超上限时回执 code=1001。
    """

    message: int
    emoji: str
    op: str


class ChatReactionUpdatePayload(TypedDict, total=False):
    """表情回应广播帧（下行）：房间内所有可访问该消息的连接各收一帧。

    ``reactions`` 为该消息**全量**回应表（emoji → 回应用户 pk 列表），客户端收到后
    整体替换本地状态即可（幂等，无需自行合并增量）。``ts`` 为广播时刻（epoch 秒），
    客户端可据此丢弃乱序到达的旧帧。
    """

    room: int
    message: int
    reactions: dict[str, list[int]]
    ts: int


class ChatReadPayload(TypedDict, total=False):
    """已读帧：上行 {room_id} → 下行回执最新已读游标。"""

    room_id: int
    last_read_id: int


class ChatUnreadPayload(TypedDict, total=False):
    """未读红点帧（下行；仅私聊/AI 会话维护未读）。"""

    room_id: int
    unread_count: int


class PushMessagePayload(TypedDict, total=False):
    """通知推送载荷（message_type 语义见 message/notifications.py）。"""

    message_type: str
    title: str
    message: str
    level: str
    notice_type: dict[str, Any]
    pk: str
    sender: str | None
    recipients: list[Any]


class TaskLogPayload(TypedDict):
    """任务执行日志增量帧（system/ws.py 每轮推送一次）。"""

    offset: int
    content: str
    finished: bool


class ScreenCommandPayload(TypedDict, total=False):
    """大屏远程控制帧（ws/screen/<pk> 下行，管理端触发）。

    command: switch（切到指定仪表盘）/ page（翻到指定页）/ refresh（重拉数据）/ auto（恢复轮播）/
    state（连接时回放当前控制态）；mode=manual 时展示端停轮播、停在 index 页。
    """

    command: str
    mode: str
    index: int
    refresh_rev: int
    rev: int
    ts: str


class ScreenDataPayload(TypedDict, total=False):
    """大屏聚合数据帧（ws/screen/<pk> 下行，按观察者各自计算后自推）。

    服务端不做组广播数据：execute/aggregate 的数据权限绑定浏览者，触发事件到达
    各展示连接后以**连接自身用户**视角聚合（dataset/screen_data.py），再只发给
    自己——权限语义与旧「客户端逐卡 HTTP 重拉」逐字节等价，M 卡 × N 观察者的
    HTTP 请求收敛为每观察者每轮 1 帧。

    canvas（Screen.layout 非空）单帧 dashboard=None；carousel（layout 空）按展示
    连接上报的当前页聚合单帧（dashboard=仪表盘 pk；未上报/越界回退全页帧）。
    cards.data 为 execute/aggregate 的返回结构；单卡失败（数据集被删 / 字段权限
    fail-closed 等）进 errors，不中断整帧。
    """

    screen: str
    dashboard: str | None
    rev: int
    cards: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    ts: int


class ScreenPageStatePayload(TypedDict, total=False):
    """大屏展示端当前页上报（上行）：carousel 轮播的当前页由展示端本地推进
    （auto 模式服务端控制态不含翻页轨迹），触发事件聚合需按**展示连接实际
    所在页**取数——不上报会导致 (N-1)/N 的聚合查询白跑。

    index：当前页码（0 基，按 Screen.dashboards 原序）；canvas 画布模式无页概念，
    展示端不上报（或 index=-1），服务端忽略。非法/越界值服务端丢弃并回退全页聚合。
    """

    index: int


class MonitorPushPayload(TypedDict, total=False):
    """监控指标推送帧（system/ws_monitor.py）。

    section=live：主机实时快照（高频，psutil 直读）；
    section=panel：服务健康 / Redis / Celery / 慢请求 / 趋势（低频重采集）。
    """

    section: str
    live: dict[str, Any]
    services: dict[str, Any]
    redis: dict[str, Any]
    celery: dict[str, Any]
    slow: dict[str, Any]
    trend: list[dict[str, Any]]
