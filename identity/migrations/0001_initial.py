# identity 域初始迁移（迁移链最前端：system.0001 经 swappable 依赖本迁移）。
#
# 由 4.x 迁移链合并生成（原 identity 0001_initial / 0002_deptinfo_rules_userinfo_rules_userrole_menu），
# 表结构与 4.2.5 完全等价（pg_dump --schema-only 逐行比对）。存量库升级由
# ``manage.py upgrade_ledger`` 认领迁移账本，本文件不参与 DDL 执行。
#
# 首操作先行创建 pg_trgm 扩展——后续模型 Meta 的 trgm GinIndex DDL 依赖它；
# 扩展不可用仅告警（对应索引建失败时全局检索回退顺序扫描），不阻断迁移。

import logging
import uuid

import django.contrib.auth.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import common.core.models
import common.fields.image
import identity.models.user

logger = logging.getLogger(__name__)


def _ensure_trgm_extension(apps, schema_editor):
    """CREATE EXTENSION IF NOT EXISTS pg_trgm（本迁移首操作）。"""
    if schema_editor.connection.vendor != "postgresql":
        return
    from system.search_indexes import TRGM_EXTENSION_SQL

    try:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(TRGM_EXTENSION_SQL)
    except Exception:  # noqa: BLE001 扩展不可用不阻断迁移
        logger.warning("pg_trgm unavailable; trgm indexes will be skipped", exc_info=True)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(_ensure_trgm_extension, migrations.RunPython.noop),
        migrations.CreateModel(
            name="AccountRisk",
            fields=[
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("user_display", models.CharField(blank=True, default="", max_length=128, verbose_name="User display")),
                (
                    "risk_type",
                    models.CharField(
                        choices=[
                            ("password_expired", "Password expired"),
                            ("password_stale", "Password not changed for a long time"),
                            ("login_stale", "No login for a long time"),
                            ("never_logged_in", "Never logged in"),
                            ("superuser_no_mfa", "Admin without MFA"),
                            ("superuser_count", "Abnormal superuser count"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="Risk type",
                    ),
                ),
                (
                    "level",
                    models.CharField(
                        choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")],
                        default="medium",
                        max_length=8,
                        verbose_name="Level",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("resolved", "Resolved"), ("ignored", "Ignored")],
                        db_index=True,
                        default="pending",
                        max_length=16,
                        verbose_name="Status",
                    ),
                ),
                ("detail", models.JSONField(blank=True, default=dict, verbose_name="Detail")),
                ("handled_at", models.DateTimeField(blank=True, null=True, verbose_name="Handled at")),
                ("remark", models.CharField(blank=True, default="", max_length=255, verbose_name="Remark")),
            ],
            options={
                "verbose_name": "Account risk",
                "verbose_name_plural": "Account risk",
                "ordering": ["-created_time"],
            },
        ),
        migrations.CreateModel(
            name="ApiApplication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("name", models.CharField(max_length=128, verbose_name="Application name")),
                ("client_id", models.CharField(max_length=64, unique=True, verbose_name="Client id")),
                ("client_secret_hash", models.CharField(max_length=64, verbose_name="Client secret hash")),
                ("client_secret_prefix", models.CharField(max_length=16, verbose_name="Client secret prefix")),
                (
                    "callback_secret_encrypted",
                    models.CharField(blank=True, default="", max_length=256, verbose_name="Callback secret"),
                ),
                ("scopes", models.JSONField(blank=True, default=list, verbose_name="Scopes")),
                ("ip_allowlist", models.JSONField(blank=True, default=list, verbose_name="Ip allowlist")),
                ("rate_limit_per_minute", models.IntegerField(default=0, verbose_name="Rate limit per minute")),
                ("callback_urls", models.JSONField(blank=True, default=list, verbose_name="Callback urls")),
                ("token_ttl_seconds", models.IntegerField(default=7200, verbose_name="Token ttl seconds")),
                ("daily_quota", models.IntegerField(default=0, verbose_name="Daily quota")),
                ("quota_alert_percent", models.IntegerField(default=80, verbose_name="Quota alert percent")),
                (
                    "daily_quota_hard",
                    models.BooleanField(
                        default=False,
                        help_text="Reject requests with 429 once the daily quota is exceeded (off = warn only)",
                        verbose_name="Enforce daily quota",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Is active")),
                ("expired_at", models.DateTimeField(blank=True, null=True, verbose_name="Expired at")),
            ],
            options={
                "verbose_name": "API application",
                "verbose_name_plural": "API application",
                "ordering": ("-created_time",),
            },
        ),
        migrations.CreateModel(
            name="ApiApplicationGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("model", models.CharField(max_length=128, verbose_name="Model")),
                ("actions", models.JSONField(default=list, verbose_name="Actions")),
                ("fields", models.JSONField(blank=True, default=list, verbose_name="Fields")),
                ("row_filter", models.JSONField(blank=True, default=list, verbose_name="Row filter")),
                ("is_active", models.BooleanField(default=True, verbose_name="Is active")),
            ],
            options={
                "verbose_name": "API application grant",
                "verbose_name_plural": "API application grant",
                "ordering": ("model", "created_time"),
            },
        ),
        migrations.CreateModel(
            name="DeptInfo",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("name", models.CharField(max_length=128, verbose_name="Department name")),
                ("code", models.CharField(max_length=128, unique=True, verbose_name="Department code")),
                ("rank", models.IntegerField(default=99, verbose_name="Rank")),
                (
                    "auto_bind",
                    models.BooleanField(
                        default=False,
                        help_text="If the value of the registration parameter channel is consistent with the department code, the user is automatically bound to the department",
                        verbose_name="Auto bind",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Is active")),
            ],
            options={
                "verbose_name": "Department",
                "verbose_name_plural": "Department",
                "ordering": ("-rank", "-created_time"),
            },
        ),
        migrations.CreateModel(
            name="DeptManagerAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, verbose_name="Created time")),
            ],
            options={
                "verbose_name": "Department manager assignment",
                "verbose_name_plural": "Department manager assignment",
                "ordering": ("-created_time",),
            },
        ),
        migrations.CreateModel(
            name="LdapUserBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("dn", models.CharField(db_index=True, max_length=512, unique=True, verbose_name="Distinguished name")),
                ("synced_at", models.DateTimeField(blank=True, null=True, verbose_name="Last synced at")),
            ],
            options={
                "verbose_name": "LDAP binding",
                "verbose_name_plural": "LDAP bindings",
                "ordering": ["-created_time"],
            },
        ),
        migrations.CreateModel(
            name="LoginAccessPolicy",
            fields=[
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=64, verbose_name="Policy name")),
                ("priority", models.IntegerField(db_index=True, default=100, verbose_name="Priority")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="Is active")),
                (
                    "target_type",
                    models.CharField(
                        choices=[("all", "All users"), ("user", "Specified users"), ("role", "Specified roles")],
                        default="all",
                        max_length=8,
                        verbose_name="Target type",
                    ),
                ),
                ("target_value", models.CharField(blank=True, default="", max_length=512, verbose_name="Target value")),
                ("weekdays", models.JSONField(blank=True, default=list, verbose_name="Weekdays")),
                ("start_time", models.TimeField(blank=True, null=True, verbose_name="Start time")),
                ("end_time", models.TimeField(blank=True, null=True, verbose_name="End time")),
                ("ip_ranges", models.TextField(blank=True, default="", verbose_name="IP ranges")),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("accept", "Accept"),
                            ("reject", "Reject"),
                            ("require_mfa", "Require MFA"),
                            ("record", "Record only"),
                        ],
                        default="reject",
                        max_length=16,
                        verbose_name="Action",
                    ),
                ),
                ("remark", models.CharField(blank=True, default="", max_length=255, verbose_name="Remark")),
            ],
            options={
                "verbose_name": "Login access policy",
                "verbose_name_plural": "Login access policy",
                "ordering": ["priority", "created_time"],
            },
        ),
        migrations.CreateModel(
            name="OAuthRefreshToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("token_hash", models.CharField(max_length=64, unique=True, verbose_name="Token hash")),
                ("scopes", models.JSONField(blank=True, default=list, verbose_name="Scopes")),
                ("expired_at", models.DateTimeField(blank=True, null=True, verbose_name="Expired at")),
                ("is_revoked", models.BooleanField(default=False, verbose_name="Is revoked")),
            ],
            options={
                "verbose_name": "OAuth refresh token",
                "verbose_name_plural": "OAuth refresh token",
                "ordering": ("-created_time",),
            },
        ),
        migrations.CreateModel(
            name="PasswordHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("password", models.CharField(max_length=128, verbose_name="Password hash")),
            ],
            options={
                "verbose_name": "Password history",
                "verbose_name_plural": "Password history",
                "ordering": ("-created_time",),
            },
        ),
        migrations.CreateModel(
            name="PersonalAccessToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("name", models.CharField(max_length=128, verbose_name="Token name")),
                ("token_hash", models.CharField(max_length=64, unique=True, verbose_name="Token hash")),
                ("token_prefix", models.CharField(max_length=16, verbose_name="Token prefix")),
                ("scopes", models.JSONField(blank=True, default=list, verbose_name="Scopes")),
                ("ip_allowlist", models.JSONField(blank=True, default=list, verbose_name="Ip allowlist")),
                ("is_active", models.BooleanField(default=True, verbose_name="Is active")),
                ("expired_at", models.DateTimeField(blank=True, null=True, verbose_name="Expired at")),
                ("last_used_time", models.DateTimeField(blank=True, null=True, verbose_name="Last used time")),
            ],
            options={
                "verbose_name": "Personal access token",
                "verbose_name_plural": "Personal access token",
                "ordering": ("-created_time",),
            },
        ),
        migrations.CreateModel(
            name="Post",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Deleted at")),
                ("name", models.CharField(max_length=64, verbose_name="Post name")),
                ("code", models.CharField(max_length=64, verbose_name="Post code")),
                ("rank", models.IntegerField(default=99, verbose_name="Rank")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="Is active")),
            ],
            options={
                "verbose_name": "Post",
                "verbose_name_plural": "Post",
                "ordering": ("rank", "name"),
            },
        ),
        migrations.CreateModel(
            name="UserOAuthBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("provider", models.CharField(db_index=True, max_length=64, verbose_name="Provider")),
                (
                    "subject",
                    models.CharField(help_text="IdP 侧唯一标识（sub）", max_length=255, verbose_name="Subject"),
                ),
                (
                    "profile",
                    models.JSONField(
                        blank=True, default=dict, help_text="IdP 返回的头像/昵称/邮箱等只读快照", verbose_name="Profile"
                    ),
                ),
            ],
            options={
                "verbose_name": "OAuth binding",
                "verbose_name_plural": "OAuth bindings",
                "ordering": ["-created_time"],
            },
        ),
        migrations.CreateModel(
            name="UserPasskey",
            fields=[
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("credential_id", models.CharField(max_length=512, unique=True, verbose_name="Credential id")),
                ("public_key", models.BinaryField(verbose_name="Public key")),
                ("sign_count", models.IntegerField(default=0, verbose_name="Sign count")),
                ("name", models.CharField(blank=True, default="", max_length=64, verbose_name="Device name")),
                ("aaguid", models.CharField(blank=True, default="", max_length=64, verbose_name="AAGUID")),
                ("backed_up", models.BooleanField(default=False, verbose_name="Backed up")),
                ("last_used_at", models.DateTimeField(blank=True, null=True, verbose_name="Last used at")),
            ],
            options={
                "verbose_name": "User passkey",
                "verbose_name_plural": "User passkey",
                "ordering": ["-created_time"],
            },
        ),
        migrations.CreateModel(
            name="UserRole",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Deleted at")),
                ("name", models.CharField(max_length=128, verbose_name="Role name")),
                ("code", models.CharField(max_length=128, verbose_name="Role code")),
                ("is_active", models.BooleanField(default=True, verbose_name="Is active")),
                ("builtin", models.BooleanField(default=False, verbose_name="Is builtin")),
                ("mfa_required", models.BooleanField(default=False, verbose_name="MFA required")),
                ("allowed_mfa_types", models.JSONField(blank=True, default=list, verbose_name="Allowed MFA types")),
            ],
            options={
                "verbose_name": "User role",
                "verbose_name_plural": "User role",
                "ordering": ("-created_time",),
            },
        ),
        migrations.CreateModel(
            name="UserSession",
            fields=[
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[("ONLINE", "Online"), ("OFFLINE", "Offline")],
                        db_index=True,
                        default="ONLINE",
                        max_length=16,
                        verbose_name="Status",
                    ),
                ),
                ("channel_name", models.CharField(blank=True, default="", max_length=128, verbose_name="WS channel")),
                ("ipaddress", models.CharField(blank=True, default="", max_length=128, verbose_name="Login IP")),
                ("city", models.CharField(blank=True, default="", max_length=128, verbose_name="Login city")),
                ("browser", models.CharField(blank=True, default="", max_length=128, verbose_name="Browser")),
                ("system", models.CharField(blank=True, default="", max_length=128, verbose_name="System")),
                ("agent", models.CharField(blank=True, default="", max_length=512, verbose_name="User agent")),
                (
                    "login_type",
                    models.IntegerField(
                        choices=[
                            (0, "Username and password"),
                            (1, "SMS verification code"),
                            (2, "Email verification code"),
                            (4, "Wechat scan code"),
                            (5, "Third-party OAuth"),
                            (3, "LDAP directory account"),
                            (8, "Websocket"),
                            (9, "Unknown"),
                            (6, "Impersonation"),
                        ],
                        default=9,
                        verbose_name="Login type",
                    ),
                ),
                (
                    "last_active",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name="Last active"),
                ),
            ],
            options={
                "verbose_name": "User session",
                "ordering": ["-last_active"],
            },
        ),
        migrations.CreateModel(
            name="UserInfo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                ("last_login", models.DateTimeField(blank=True, null=True, verbose_name="last login")),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text="Designates that this user has all permissions without explicitly assigning them.",
                        verbose_name="superuser status",
                    ),
                ),
                (
                    "username",
                    models.CharField(
                        error_messages={"unique": "A user with that username already exists."},
                        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
                        max_length=150,
                        unique=True,
                        validators=[django.contrib.auth.validators.UnicodeUsernameValidator()],
                        verbose_name="username",
                    ),
                ),
                ("first_name", models.CharField(blank=True, max_length=150, verbose_name="first name")),
                ("last_name", models.CharField(blank=True, max_length=150, verbose_name="last name")),
                (
                    "is_staff",
                    models.BooleanField(
                        default=False,
                        help_text="Designates whether the user can log into this admin site.",
                        verbose_name="staff status",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Designates whether this user should be treated as active. Unselect this instead of deleting accounts.",
                        verbose_name="active",
                    ),
                ),
                ("date_joined", models.DateTimeField(default=django.utils.timezone.now, verbose_name="date joined")),
                ("created_time", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created time")),
                ("updated_time", models.DateTimeField(auto_now=True, null=True, verbose_name="Updated time")),
                ("description", models.CharField(blank=True, max_length=256, null=True, verbose_name="Description")),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Deleted at")),
                (
                    "avatar",
                    common.fields.image.ProcessedImageField(
                        blank=True, null=True, upload_to=common.core.models.upload_directory_path, verbose_name="Avatar"
                    ),
                ),
                ("nickname", models.CharField(blank=True, max_length=150, verbose_name="Nickname")),
                (
                    "gender",
                    models.IntegerField(
                        choices=[(0, "Unknown"), (1, "Male"), (2, "Female")], default=0, verbose_name="Gender"
                    ),
                ),
                ("phone", models.CharField(blank=True, db_index=True, default="", max_length=16, verbose_name="Phone")),
                (
                    "email",
                    models.EmailField(blank=True, db_index=True, default="", max_length=254, verbose_name="Email"),
                ),
                (
                    "mfa_level",
                    models.IntegerField(choices=[(0, "Disabled"), (1, "Enabled")], default=0, verbose_name="MFA level"),
                ),
                (
                    "otp_secret_key",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="OTP secret key"),
                ),
                ("allowed_mfa_types", models.JSONField(blank=True, default=list, verbose_name="Allowed MFA types")),
                ("must_change_password", models.BooleanField(default=False, verbose_name="Must change password")),
                (
                    "date_password_updated",
                    models.DateTimeField(blank=True, null=True, verbose_name="Password updated at"),
                ),
                (
                    "date_expired",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Account expiry"),
                ),
                (
                    "invite_status",
                    models.CharField(
                        blank=True,
                        choices=[("pending", "Pending acceptance"), ("accepted", "Accepted")],
                        default="",
                        max_length=16,
                        verbose_name="Invite status",
                    ),
                ),
                ("invited_time", models.DateTimeField(blank=True, null=True, verbose_name="Invited at")),
                (
                    "creator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        related_query_name="creator_query",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creator",
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "modifier",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        related_query_name="modifier_query",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modifier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Userinfo",
                "verbose_name_plural": "Userinfo",
                "ordering": ("-date_joined",),
            },
            bases=(common.core.models.AutoCleanFileMixin, models.Model),
            managers=[
                ("objects", identity.models.user.SoftDeleteUserManager()),
            ],
        ),
    ]
