#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码请求 / 响应序列化器（OpenAPI 契约来源）。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from captcha.const import CAPTCHA_SCENES, CAPTCHA_TYPES
from captcha.models import CaptchaLog, CaptchaRule
from common.core.fields import LabeledChoiceField
from common.core.serializers import BaseModelSerializer


class CaptchaScreenSerializer(serializers.Serializer):
    """客户端屏幕信息（只用于风险信号，数值越界/类型错误在入口即拒绝）。"""

    width = serializers.IntegerField(required=False, min_value=0, max_value=20000, label=_("Screen width"))
    height = serializers.IntegerField(required=False, min_value=0, max_value=20000, label=_("Screen height"))
    availWidth = serializers.IntegerField(required=False, min_value=0, max_value=20000, label=_("Available width"))
    availHeight = serializers.IntegerField(required=False, min_value=0, max_value=20000, label=_("Available height"))
    dpr = serializers.FloatField(required=False, min_value=0, max_value=10, label=_("Device pixel ratio"))


class CaptchaEnvSerializer(serializers.Serializer):
    """客户端环境包；字段与前端 captchaSignal/env.ts 对齐，畸形结构直接 400。"""

    v = serializers.IntegerField(required=False, min_value=0, max_value=10, label=_("Env version"))
    timezone = serializers.CharField(required=False, allow_blank=True, max_length=64, label=_("Timezone"))
    language = serializers.CharField(required=False, allow_blank=True, max_length=64, label=_("Language"))
    screen = CaptchaScreenSerializer(required=False, allow_null=True, label=_("Screen"))
    touch = serializers.IntegerField(required=False, min_value=0, max_value=100, label=_("Touch points"))
    cores = serializers.IntegerField(required=False, min_value=0, max_value=256, label=_("CPU cores"))
    reduced_motion = serializers.BooleanField(required=False, label=_("Reduced motion"))
    webdriver = serializers.BooleanField(required=False, label=_("WebDriver"))
    plugins = serializers.IntegerField(required=False, min_value=0, max_value=1000, label=_("Plugin count"))
    referer = serializers.BooleanField(required=False, label=_("Referer present"))
    canvas = serializers.CharField(required=False, allow_blank=True, max_length=128, label=_("Canvas digest"))
    webgl = serializers.CharField(required=False, allow_blank=True, max_length=128, label=_("WebGL digest"))
    fp = serializers.CharField(required=False, allow_blank=True, max_length=128, label=_("Client fingerprint"))


class CaptchaChallengeInSerializer(serializers.Serializer):
    type = serializers.ChoiceField(
        choices=CAPTCHA_TYPES,
        required=False,
        label=_("Captcha type"),
        help_text=_("Omit to let the server pick one from the enabled types"),
    )
    scene = serializers.ChoiceField(choices=CAPTCHA_SCENES, label=_("Captcha scene"))
    action = serializers.CharField(
        max_length=64,
        required=False,
        label=_("Server action"),
        help_text=_("Intent action from the scene registry; required when action binding is enabled"),
    )
    env = CaptchaEnvSerializer(
        required=False,
        allow_null=True,
        label=_("Client environment"),
    )
    account = serializers.CharField(max_length=128, required=False, allow_blank=True, label=_("Account hash"))
    hp = serializers.CharField(max_length=128, required=False, allow_blank=True, label=_("Honeypot"))


class CaptchaTrackPointSerializer(serializers.Serializer):
    x = serializers.FloatField(label=_("X coordinate"))
    y = serializers.FloatField(label=_("Y coordinate"))
    t = serializers.FloatField(label=_("Timestamp (ms)"))
    # 事件类型（down/move/up/click/keyboard）：信号层区分轨迹阶段；缺省视为 move
    ev = serializers.CharField(required=False, allow_blank=True, max_length=16, label=_("Track event"))


class CaptchaVerifyInSerializer(serializers.Serializer):
    challenge_id = serializers.CharField(max_length=64, label=_("Challenge ID"))
    type = serializers.ChoiceField(choices=CAPTCHA_TYPES, label=_("Captcha type"))
    scene = serializers.ChoiceField(choices=CAPTCHA_SCENES, label=_("Captcha scene"))
    answer = serializers.JSONField(label=_("Answer"))
    track = CaptchaTrackPointSerializer(many=True, required=False, label=_("Pointer track"))
    action = serializers.CharField(
        max_length=64,
        required=False,
        label=_("Server action"),
        help_text=_("Server-side action constant the issued ticket binds to"),
    )
    gen_time = serializers.IntegerField(
        required=False,
        label=_("Generation time"),
        help_text=_("Client epoch seconds; validated within the verify sign time window"),
    )
    nonce = serializers.CharField(
        max_length=128,
        required=False,
        label=_("Nonce"),
        help_text=_("One-time random string bound to the signed verify request"),
    )
    sign = serializers.CharField(
        max_length=128,
        required=False,
        label=_("Signature"),
        help_text=_("HMAC signature over the canonical verify payload keyed by ck"),
    )
    env = CaptchaEnvSerializer(
        required=False,
        allow_null=True,
        label=_("Client environment"),
    )
    account = serializers.CharField(max_length=128, required=False, allow_blank=True, label=_("Account hash"))
    hp = serializers.CharField(max_length=128, required=False, allow_blank=True, label=_("Honeypot"))
    input = serializers.ChoiceField(
        choices=("pointer", "keyboard"),
        required=False,
        label=_("Input mode"),
        help_text=_("Keyboard-driven answers must not be scored as bot behavior"),
    )
    pow_answer = serializers.JSONField(required=False, label=_("PoW answer"))


class CaptchaChallengeOutSerializer(serializers.Serializer):
    challenge_id = serializers.CharField(required=False, label=_("Challenge ID"))
    type = serializers.ChoiceField(choices=CAPTCHA_TYPES, required=False, label=_("Captcha type"))
    background = serializers.CharField(required=False, label=_("Background image"))
    piece = serializers.CharField(required=False, label=_("Puzzle piece"))
    tip = serializers.CharField(required=False, label=_("Click tip image"))
    width = serializers.IntegerField(required=False, label=_("Image width"))
    height = serializers.IntegerField(required=False, label=_("Image height"))
    piece_size = serializers.IntegerField(required=False, label=_("Piece size"))
    count = serializers.IntegerField(required=False, label=_("Click count"))
    length = serializers.IntegerField(required=False, label=_("Image answer length"))
    audio = serializers.BooleanField(required=False, label=_("Audio available"))
    ttl = serializers.IntegerField(label=_("Validity period (second)"))
    ts = serializers.IntegerField(
        required=False,
        label=_("Server time"),
        help_text=_("Server epoch seconds; use it as gen_time when signing the verify request"),
    )
    pow = serializers.JSONField(required=False, label=_("PoW challenge"))
    ck = serializers.CharField(
        required=False,
        label=_("Session key"),
        help_text=_("One-time verify sign key; present only when verify signing is enforced"),
    )


class CaptchaVerifyOutSerializer(serializers.Serializer):
    passed = serializers.BooleanField(label=_("Passed"))
    ticket = serializers.CharField(required=False, label=_("One-time ticket"))
    expires_in = serializers.IntegerField(required=False, label=_("Ticket TTL (second)"))


class CaptchaChallengeResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    error = serializers.CharField(required=False, label=_("Error code"))
    data = CaptchaChallengeOutSerializer()


class CaptchaVerifyResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    error = serializers.CharField(required=False, label=_("Error code"))
    data = CaptchaVerifyOutSerializer()


class CaptchaLogSerializer(BaseModelSerializer):
    """验证码观测日志（只读，供元数据协议列表页消费）。"""

    class Meta:
        model = CaptchaLog
        fields = [
            "pk",
            "ts",
            "phase",
            "scene",
            "action",
            "mode",
            "type",
            "difficulty",
            "asset_id",
            "decision",
            "would_decision",
            "risk_score",
            "risk_reasons",
            "passed",
            "attempts",
            "latency_ms",
            "ip_hash",
            "fp_hash",
            "ua_class",
            "browser",
            "env_version",
            "pow_bits",
            "key_id",
        ]
        table_fields = [
            "ts",
            "phase",
            "scene",
            "action",
            "type",
            "asset_id",
            "decision",
            "passed",
            "risk_score",
            "attempts",
            "latency_ms",
            "ua_class",
            "browser",
        ]
        read_only_fields = list(fields)

    phase = LabeledChoiceField(choices=CaptchaLog.PhaseChoices.choices, read_only=True)


class CaptchaRuleSerializer(serializers.ModelSerializer):
    """风险规则（只读展示 + 可调分/开关/优先级）。"""

    class Meta:
        model = CaptchaRule
        fields = ["code", "name", "scope", "score", "enabled", "priority", "params"]
        read_only_fields = ["code", "name", "scope"]


class CaptchaRuleUpdateItemSerializer(serializers.Serializer):
    """规则可写字段：分值 / 开关 / 优先级。

    ``params`` 当前无运行时消费点（规则判据固定实现），因此不开放写入——
    可编辑但永不生效会让运营误以为调过窗口/阈值。分值与分数域一致限制在
    [-100, 100]，避免误填极端值把全站推向 allow/deny。
    """

    code = serializers.CharField(max_length=32, label=_("Rule code"))
    score = serializers.IntegerField(required=False, min_value=-100, max_value=100, label=_("Score"))
    enabled = serializers.BooleanField(required=False, label=_("Enabled"))
    priority = serializers.IntegerField(required=False, min_value=1, max_value=9999, label=_("Priority"))


class CaptchaRuleUpdateSerializer(serializers.Serializer):
    rules = CaptchaRuleUpdateItemSerializer(many=True, label=_("Rules"))


class CaptchaStatsInSerializer(serializers.Serializer):
    hours = serializers.IntegerField(
        required=False,
        default=24,
        min_value=1,
        max_value=168,
        label=_("Window (hours)"),
        help_text=_("Aggregation window in hours; default 24, max 168"),
    )


class CaptchaStatsTypeRowSerializer(serializers.Serializer):
    type = serializers.CharField(label=_("Captcha type"))
    total = serializers.IntegerField(label=_("Verify count"))
    passed = serializers.IntegerField(label=_("Passed count"))
    pass_rate = serializers.FloatField(label=_("Pass rate"))


class CaptchaStatsAssetRowSerializer(serializers.Serializer):
    asset_id = serializers.CharField(label=_("Asset ID"))
    total = serializers.IntegerField(label=_("Verify count"))
    passed = serializers.IntegerField(label=_("Passed count"))
    pass_rate = serializers.FloatField(label=_("Pass rate"))


class CaptchaStatsDecisionRowSerializer(serializers.Serializer):
    decision = serializers.CharField(label=_("Decision"))
    total = serializers.IntegerField(label=_("Count"))


class CaptchaStatsTrendPointSerializer(serializers.Serializer):
    hour = serializers.CharField(label=_("Hour bucket"))
    total = serializers.IntegerField(label=_("Verify count"))
    passed = serializers.IntegerField(label=_("Passed count"))


class CaptchaStatsSummarySerializer(serializers.Serializer):
    challenge = serializers.IntegerField(label=_("Challenge count"))
    verify = serializers.IntegerField(label=_("Verify count"))
    verify_passed = serializers.IntegerField(label=_("Verify passed"))
    verify_failed = serializers.IntegerField(label=_("Verify failed"))
    consume = serializers.IntegerField(label=_("Ticket consumed"))
    pass_rate = serializers.FloatField(label=_("Pass rate"))
    deny = serializers.IntegerField(label=_("Deny count"))
    deny_rate = serializers.FloatField(label=_("Deny rate"))
    latency_p50 = serializers.IntegerField(label=_("Verify latency P50 (ms)"))


class CaptchaStatsPolicySerializer(serializers.Serializer):
    risk_mode = serializers.CharField(label=_("Risk mode"))
    log_enabled = serializers.BooleanField(label=_("Log enabled"))
    retention_days = serializers.IntegerField(label=_("Retention days"))
    thresholds = serializers.DictField(child=serializers.IntegerField(), label=_("Decision thresholds"))
    pow_enabled = serializers.BooleanField(label=_("PoW enabled"))
    failure_ladder = serializers.CharField(label=_("Failure ladder"))


class CaptchaStatsRiskBucketSerializer(serializers.Serializer):
    bucket = serializers.CharField(label=_("Score bucket"))
    total = serializers.IntegerField(label=_("Count"))


class CaptchaStatsTopReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(label=_("Reason code"))
    total = serializers.IntegerField(label=_("Count"))


class CaptchaStatsTopEntitySerializer(serializers.Serializer):
    """TopN 实体行：ip_hash / fp_hash / ua_class 三选一（按查询字段投影）。"""

    ip_hash = serializers.CharField(required=False, label=_("IP hash"))
    fp_hash = serializers.CharField(required=False, label=_("Fingerprint hash"))
    ua_class = serializers.CharField(required=False, label=_("UA class"))
    total = serializers.IntegerField(label=_("Count"))


class CaptchaStatsSerializer(serializers.Serializer):
    """验证码数据大盘（时间窗聚合，只读）。"""

    window_hours = serializers.IntegerField(label=_("Window (hours)"))
    generated_at = serializers.IntegerField(label=_("Generated at"))
    policy = CaptchaStatsPolicySerializer(label=_("Policy snapshot"))
    summary = CaptchaStatsSummarySerializer(label=_("Summary"))
    by_type = CaptchaStatsTypeRowSerializer(many=True, label=_("By captcha type"))
    by_asset = CaptchaStatsAssetRowSerializer(many=True, label=_("By asset"))
    by_decision = CaptchaStatsDecisionRowSerializer(many=True, label=_("By decision"))
    risk_distribution = CaptchaStatsRiskBucketSerializer(many=True, label=_("Risk score distribution"))
    top_reasons = CaptchaStatsTopReasonSerializer(many=True, label=_("Top deny reasons"))
    top_ips = CaptchaStatsTopEntitySerializer(many=True, label=_("Top IP hashes"))
    top_fingerprints = CaptchaStatsTopEntitySerializer(many=True, label=_("Top fingerprints"))
    top_ua = CaptchaStatsTopEntitySerializer(many=True, label=_("Top UA classes"))
    trend = CaptchaStatsTrendPointSerializer(many=True, label=_("Hourly trend"))
    telemetry = serializers.DictField(child=serializers.IntegerField(), label=_("Telemetry health"))


class CaptchaStatsResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    error = serializers.CharField(required=False, label=_("Error code"))
    data = CaptchaStatsSerializer()
