import json

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.base.utils import signer
from common.core.models import DbAuditModel, DbUuidModel


class Setting(DbAuditModel, DbUuidModel):
    name = models.CharField(max_length=128, unique=True, verbose_name=_("Name"))
    value = models.TextField(verbose_name=_("Value"), null=True, blank=True)
    category = models.CharField(max_length=128, default="default", verbose_name=_('Category'))
    encrypted = models.BooleanField(default=False, verbose_name=_('Encrypted'))
    is_active = models.BooleanField(default=True, verbose_name=_("Is active"))

    def __str__(self):
        return self.name

    @property
    def cleaned_value(self):
        try:
            value = self.value
            if self.encrypted and value is not None:
                value = signer.decrypt(value)
            if not value:
                return None
            value = json.loads(value)
            return value
        except json.JSONDecodeError:
            return None

    @cleaned_value.setter
    def cleaned_value(self, item):
        try:
            if isinstance(item, set):
                item = list(item)
            v = json.dumps(item)
            if self.encrypted:
                v = signer.encrypt(v.encode('utf-8')).decode('utf-8')
            self.value = v
        except json.JSONDecodeError as e:
            raise ValueError("Json dump error: {}".format(str(e)))

    @classmethod
    def refresh_all_settings(cls):
        try:
            for setting in cls.objects.all():
                setting.refresh_setting()
        except Exception:
            pass

    @classmethod
    def refresh_item(cls, data):
        setattr(settings, data[0], data[1])

    def refresh_setting(self):
        setattr(settings, self.name, self.cleaned_value)

    @classmethod
    def save_to_file(cls, value: InMemoryUploadedFile):
        filename = value.name
        filepath = f'upload/settings/{filename}'
        path = default_storage.save(filepath, ContentFile(value.read()))
        url = default_storage.url(path)
        return url

    @classmethod
    def update_or_create(cls, name='', value='', encrypted=False, category='', user=None):
        """
        不能使用 Model 提供的，update_or_create 因为这里有 encrypted 和 cleaned_value
        :return: (changed, instance)
        """
        setting = cls.objects.filter(name=name).first()
        changed = False
        if not setting:
            setting = Setting(name=name, encrypted=encrypted, category=category, modifier=user, creator=user)

        if isinstance(value, InMemoryUploadedFile):
            value = cls.save_to_file(value)

        if setting.cleaned_value != value:
            setting.encrypted = encrypted
            setting.cleaned_value = value
            setting.modifier = user
            setting.save()
            changed = True
        return changed, setting

    class Meta:
        verbose_name = _("System setting")
        verbose_name_plural = verbose_name
