import json
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext_lazy as _

from common.base.utils import signer
from common.core.models import DbAuditModel, DbUuidModel
from common.utils import get_logger

logger = get_logger(__name__)


class Setting(DbAuditModel, DbUuidModel):
    name = models.CharField(max_length=128, unique=True, verbose_name=_("Name"))
    value = models.TextField(verbose_name=_("Value"), null=True, blank=True)
    category = models.CharField(max_length=128, default="default", verbose_name=_("Category"))
    encrypted = models.BooleanField(default=False, verbose_name=_("Encrypted"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is active"))

    def __str__(self) -> str:
        return str(self.name)

    @property
    def cleaned_value(self) -> Any:
        try:
            value = self.value
            if self.encrypted and value is not None:
                value = signer.decrypt(value)
            if not value:
                return None
            value = json.loads(value)
            return value
        except (json.JSONDecodeError, ValueError):
            # json 损坏 / 密文认证失败（GCM 校验不过抛 ValueError）：按读取失败回落 None，
            # 不让单条坏配置把设置读取打成 500
            return None

    @cleaned_value.setter
    def cleaned_value(self, item: Any) -> None:
        try:
            if isinstance(item, set):
                item = list(item)
            v = json.dumps(item)
            if self.encrypted:
                v = signer.encrypt(v.encode("utf-8")).decode("utf-8")
            self.value = v
        except json.JSONDecodeError as e:
            raise ValueError(f"Json dump error: {str(e)}") from e

    @classmethod
    def refresh_all_settings(cls) -> None:
        """批量刷新设置项到运行时 settings：逐条容错。

        单条设置损坏（密文认证失败 / 值不可序列化）只记日志、不中断其余设置，
        也不向调用方抛出（启动与保存流程不因单条坏配置失败）。

        表不存在（新装环境 migrate 之前 / CI 对裸库 ``manage.py check``）时整体
        跳过：django_ready 后台线程里的查询会以 UndefinedTable（PG）/
        OperationalError「no such table」（sqlite）裸崩，逐条容错管不到查询本身
        （2026-10-01 真环境档暴露，处置登记见
        docs/plans/全真容器化测试迁移方案-2026.10.md §五 #21）。表存在性经
        introspection 前置探测，方言无关。
        """
        from django.db import connection

        if cls._meta.db_table not in connection.introspection.table_names():
            logger.warning(
                "settings table %s missing; skip refresh_all_settings (database not migrated?)",
                cls._meta.db_table,
            )
            return
        for setting in cls.objects.all():
            try:
                setting.refresh_setting()
            except Exception:  # noqa: BLE001 单条损坏不中断批量刷新
                logger.warning("refresh setting failed: %s", setting.name, exc_info=True)

    @classmethod
    def refresh_item(cls, data: Any) -> None:
        setattr(settings, data[0], data[1])

    @classmethod
    def refresh_names(cls, names: Iterable[str]) -> None:
        """从库回读指定设置行并应用为本进程运行时值（对账原语）。

        Setting 行热更依赖 pubsub 广播回写各进程，丢消息时本进程 settings 会停留
        旧值；按行名回读（唯一索引，行数极小）让消费方以固定周期自愈收敛。
        """
        for setting in cls.objects.filter(name__in=names):
            try:
                setting.refresh_setting()
            except Exception:  # noqa: BLE001 单条损坏不中断批量刷新（与 refresh_all_settings 同口径）
                logger.warning("refresh setting failed: %s", setting.name, exc_info=True)

    @classmethod
    def default_value(cls, name: str) -> Any:
        """行删除后的运行时回收值：同名静态配置默认值（config.yml / 环境变量 / 代码默认值）。

        CONFIG 经 common.injection 注入，不反向 import server。键完全
        未知（运行期自建的自定义键）时回落 None：回收统一走 setattr 语义（pub/sub
        载荷须可 JSON 序列化），未知键的运行时属性收敛为 None 而非删除属性。
        """
        from common.injection import get_server_config

        return get_server_config().get(name)

    def refresh_setting(self) -> None:
        setattr(settings, self.name, self.cleaned_value)

    @classmethod
    def save_to_file(cls, value: InMemoryUploadedFile) -> str:
        filename = value.name
        filepath = f"upload/settings/{filename}"
        path = default_storage.save(filepath, ContentFile(value.read()))
        url: str = default_storage.url(path)
        return url

    @classmethod
    def update_or_create(
        cls, name: str = "", value: Any = "", encrypted: bool = False, category: str = "", user: Any = None
    ) -> tuple[bool, "Setting | None"]:
        """
        不能使用 Model 提供的，update_or_create 因为这里有 encrypted 和 cleaned_value
        :return: (changed, instance)
        """
        # 「先查后插」不是原子操作，并发写同名键会双双查空后各自插入：
        # select_for_update 锁不到尚不存在的行（SQLite 上更是 no-op），插入撞
        # name 唯一约束时回滚本事务并重查一次改走更新分支（限一次重试防死循环）。
        for _attempt in range(2):
            try:
                with transaction.atomic():
                    setting = cls.objects.select_for_update().filter(name=name).first()
                    changed = False
                    if not setting:
                        setting = Setting(
                            name=name, encrypted=encrypted, category=category, modifier=user, creator=user
                        )

                    if isinstance(value, InMemoryUploadedFile):
                        value = cls.save_to_file(value)

                    if setting.cleaned_value != value:
                        setting.encrypted = encrypted
                        setting.cleaned_value = value
                        setting.modifier = user
                        setting.save()
                        changed = True
                    return changed, setting
            except IntegrityError:
                continue
        # 连续两次撞唯一约束的极端并发下不再尝试写入：按未变更返回当前行，
        # 调用方只在 changed 为真时才使用实例，不向其抛出数据库异常。
        return False, cls.objects.filter(name=name).first()

    class Meta:
        verbose_name = _("System setting")
        verbose_name_plural = verbose_name
