import datetime
import hashlib
import json

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from pilkit.processors import ResizeToFill
from rest_framework.utils import encoders

from common.core.models import upload_directory_path, DbAuditModel, DbUuidModel, DbCharModel
from common.fields.image import ProcessedImageField


class ModelLabelField(DbAuditModel, DbUuidModel):
    class KeyChoices(models.TextChoices):
        TEXT = 'value.text', _('Text')
        JSON = 'value.json', _('Json')
        ALL = 'value.all', _('All data')
        DATETIME = 'value.datetime', _('Datetime')
        DATETIME_RANGE = 'value.datetime.range', _('Datetime range selector')
        DATE = 'value.date', _('Seconds to the current time')
        OWNER = 'value.user.id', _('My ID')
        OWNER_DEPARTMENT = 'value.user.dept.id', _('My department ID')
        OWNER_DEPARTMENTS = 'value.user.dept.ids', _('My department ID and data below the department')
        DEPARTMENTS = 'value.dept.ids', _('Department ID and data below the department')
        TABLE_USER = 'value.table.user.ids', _('Select the user ID')
        TABLE_MENU = 'value.table.menu.ids', _('Select menu ID')
        TABLE_ROLE = 'value.table.role.ids', _('Select role ID')
        TABLE_DEPT = 'value.table.dept.ids', _('Select department ID')

    class FieldChoices(models.IntegerChoices):
        ROLE = 0, _("Role Permission")
        DATA = 1, _("Data Permission")

    field_type = models.SmallIntegerField(choices=FieldChoices, default=FieldChoices.DATA, verbose_name=_("Field Type"))
    parent = models.ForeignKey(to='ModelLabelField', on_delete=models.CASCADE, null=True, blank=True,
                               verbose_name=_("Parent node"))
    name = models.CharField(verbose_name=_("Model/Field name"), max_length=128)
    label = models.CharField(verbose_name=_("Model/Field label"), max_length=128)

    class Meta:
        unique_together = ('name', 'parent')
        verbose_name = _("Model Label Field")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.label}({self.name})"


class ModeTypeAbstract(models.Model):
    class ModeChoices(models.IntegerChoices):
        OR = 0, _("Or Mode")
        AND = 1, _("And Mode")

    mode_type = models.SmallIntegerField(choices=ModeChoices, default=ModeChoices.OR,
                                         verbose_name=_("Data Permission Mode"),
                                         help_text=_(
                                             "Permission mode, and the mode indicates that the data needs to satisfy each rule in the rule list at the same time, or the mode satisfies any rule"))

    class Meta:
        abstract = True


class UserInfo(DbAuditModel, AbstractUser, ModeTypeAbstract):
    class GenderChoices(models.IntegerChoices):
        UNKNOWN = 0, _("Unknown")
        MALE = 1, _("Male")
        FEMALE = 2, _("Female")

    avatar = ProcessedImageField(verbose_name=_("Avatar"), null=True, blank=True,
                                 upload_to=upload_directory_path,
                                 processors=[ResizeToFill(512, 512)],  # 默认存储像素大小
                                 scales=[1, 2, 3, 4],  # 缩略图可缩小倍数，
                                 format='png')

    nickname = models.CharField(verbose_name=_("Nickname"), max_length=150, blank=True)
    gender = models.IntegerField(choices=GenderChoices, default=GenderChoices.UNKNOWN, verbose_name=_("Gender"))
    mobile = models.CharField(verbose_name=_("Mobile"), max_length=16, default='', blank=True)

    roles = models.ManyToManyField(to="UserRole", verbose_name=_("Role Permission"), blank=True, null=True)
    rules = models.ManyToManyField(to="DataPermission", verbose_name=_("Data Permission"), blank=True, null=True)
    dept = models.ForeignKey(to="DeptInfo", verbose_name=_("Department"), on_delete=models.PROTECT, blank=True,
                             null=True,
                             related_query_name="dept_query")

    class Meta:
        verbose_name = _("Userinfo")
        verbose_name_plural = verbose_name
        ordering = ("-date_joined",)

    def __str__(self):
        return f"{self.nickname}({self.username})"


class MenuMeta(DbAuditModel, DbUuidModel):
    title = models.CharField(verbose_name=_("Menu Title"), max_length=255, null=True, blank=True)
    icon = models.CharField(verbose_name=_("Left Icon"), max_length=255, null=True, blank=True)
    r_svg_name = models.CharField(verbose_name=_("Right Icon"), max_length=255, null=True, blank=True,
                                  help_text=_("Additional icon to the right of menu name"))
    is_show_menu = models.BooleanField(verbose_name=_("Show Menu"), default=True)
    is_show_parent = models.BooleanField(verbose_name=_("Show Parent Menu"), default=False)
    is_keepalive = models.BooleanField(verbose_name=_("Keepalive"), default=False,
                                       help_text=_(
                                           "When enabled, the entire state of the page is saved, and when refreshed, the state is cleared"))
    frame_url = models.CharField(verbose_name=_("Iframe URL"), max_length=255, null=True, blank=True,
                                 help_text=_("The embedded iframe link address"))
    frame_loading = models.BooleanField(verbose_name=_("Iframe Loading"), default=False)

    transition_enter = models.CharField(verbose_name=_("Enter Animation"), max_length=255, null=True, blank=True)
    transition_leave = models.CharField(verbose_name=_("Leave Animation"), max_length=255, null=True, blank=True)

    is_hidden_tag = models.BooleanField(verbose_name=_("Hidden Tag"), default=False, help_text=_(
        "The current menu name or custom information is prohibited from being added to the TAB"))
    fixed_tag = models.BooleanField(verbose_name=_("Fixed Tag"), default=False, help_text=_(
        "Whether the current menu name is fixed to the TAB and cannot be closed"))
    dynamic_level = models.IntegerField(verbose_name=_("Dynamic Level"), default=0,
                                        help_text=_("Maximum number of dynamic routes that can be opened"))

    class Meta:
        verbose_name = _("Menu Meta")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.title}-{self.description}"


class Menu(DbAuditModel, DbUuidModel):
    class MenuChoices(models.IntegerChoices):
        DIRECTORY = 0, _("Directory")
        MENU = 1, _("Menu")
        PERMISSION = 2, _("Permission")

    class MethodChoices(models.TextChoices):
        GET = 'GET', _("GET")
        POST = 'POST', _("POST")
        PUT = 'PUT', _("PUT")
        DELETE = 'DELETE', _("DELETE")
        PATCH = 'PATCH', _("PATCH")

    parent = models.ForeignKey(to='Menu', on_delete=models.SET_NULL, verbose_name=_("Parent Menu"), null=True,
                               blank=True)
    menu_type = models.SmallIntegerField(choices=MenuChoices, default=MenuChoices.DIRECTORY,
                                         verbose_name=_("Menu Type"))
    name = models.CharField(verbose_name=_("Component Name Or Permission Code"), max_length=128, unique=True)
    rank = models.IntegerField(verbose_name=_("Rank"), default=9999)
    path = models.CharField(verbose_name=_("Route Path Or Api Path"), max_length=255)
    component = models.CharField(verbose_name=_("Component Path"), max_length=255, null=True, blank=True)
    is_active = models.BooleanField(verbose_name=_("Is Active"), default=True)
    meta = models.OneToOneField(to=MenuMeta, on_delete=models.CASCADE, verbose_name=_("Menu Meta"))
    model = models.ManyToManyField(to=ModelLabelField, verbose_name=_("Model"), null=True, blank=True)

    # permission_marking = models.CharField(verbose_name="权限标识", max_length=255)
    # api_route = models.CharField(verbose_name="后端权限路由", max_length=255, null=True, blank=True)
    method = models.CharField(choices=MethodChoices, null=True, blank=True, verbose_name=_("Method"), max_length=10)

    # api_auth_access = models.BooleanField(verbose_name="是否授权访问，否的话可以匿名访问后端路由", default=True)

    def delete(self, using=None, keep_parents=False):
        if self.meta:
            self.meta.delete(using, keep_parents)
        super().delete(using, keep_parents)

    class Meta:
        verbose_name = _("Menu")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.meta.title}-{self.get_menu_type_display()}({self.name})"


class DataPermission(DbAuditModel, ModeTypeAbstract, DbUuidModel):
    name = models.CharField(verbose_name=_("Name"), max_length=255, unique=True)
    rules = models.JSONField(verbose_name=_("Rules"), max_length=512)
    is_active = models.BooleanField(verbose_name=_("Is Active"), default=True)
    menu = models.ManyToManyField(to=Menu, verbose_name=_("Menu"), null=True, blank=True,
                                  help_text=_("If a menu exists, it only applies to the selected menu permission"))

    class Meta:
        verbose_name = _("Data Permission")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name}"


class UserRole(DbAuditModel, DbUuidModel):
    name = models.CharField(max_length=128, verbose_name=_("Role Name"), unique=True)
    code = models.CharField(max_length=128, verbose_name=_("Role Code"), unique=True)
    is_active = models.BooleanField(verbose_name=_("Is Active"), default=True)
    menu = models.ManyToManyField(to='Menu', verbose_name=_("Menu"), null=True, blank=True)

    class Meta:
        verbose_name = _("User Role")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}({self.code})"


class FieldPermission(DbAuditModel, DbCharModel):
    role = models.ForeignKey(UserRole, on_delete=models.CASCADE, verbose_name=_("Role"))
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, verbose_name=_("Menu"))
    field = models.ManyToManyField(ModelLabelField, verbose_name=_("Field"), null=True, blank=True)

    class Meta:
        verbose_name = _("Field Permission")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)
        unique_together = ("role", "menu")

    def save(self, *args, **kwargs):
        self.id = f"{self.role.pk}-{self.menu.pk}"
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.pk}-{self.role.name}-{self.created_time}"


class DeptInfo(DbAuditModel, ModeTypeAbstract, DbUuidModel):
    name = models.CharField(verbose_name=_("Department Name"), max_length=128)
    code = models.CharField(max_length=128, verbose_name=_("Department Code"), unique=True)
    parent = models.ForeignKey(to='DeptInfo', on_delete=models.SET_NULL, verbose_name=_("Superior Department"),
                               null=True, blank=True, related_query_name="parent_query")
    roles = models.ManyToManyField(to="UserRole", verbose_name=_("Role Permission"), blank=True, null=True)
    rules = models.ManyToManyField(to="DataPermission", verbose_name=_("Data Permission"), blank=True, null=True)
    rank = models.IntegerField(verbose_name=_("Rank"), default=99)
    auto_bind = models.BooleanField(verbose_name=_("Auto Bind"), default=False,
                                    help_text=_(
                                        "If the value of the registration parameter channel is consistent with the department code, the user is automatically bound to the department"))
    is_active = models.BooleanField(verbose_name=_("Is Active"), default=True)

    @classmethod
    def recursion_dept_info(cls, dept_id: int, dept_all_list=None, dept_list=None, is_parent=False):
        parent = 'parent'
        pk = 'pk'
        if is_parent:
            parent, pk = pk, parent
        if not dept_all_list:
            dept_all_list = DeptInfo.objects.values("pk", "parent")
        if dept_list is None:
            dept_list = [dept_id]
        for dept in dept_all_list:
            if dept.get(parent) == dept_id:
                if dept.get(pk):
                    dept_list.append(dept.get(pk))
                    cls.recursion_dept_info(dept.get(pk), dept_all_list, dept_list, is_parent)
        return json.loads(json.dumps(list(set(dept_list)), cls=encoders.JSONEncoder))

    class Meta:
        verbose_name = _("Department")
        verbose_name_plural = verbose_name
        ordering = ("-rank", "-created_time",)

    def __str__(self):
        return f"{self.name}({self.pk})"


class UserLoginLog(DbAuditModel):
    class LoginTypeChoices(models.IntegerChoices):
        USERNAME = 0, _("Username And Password")
        SMS = 1, _("SMS Verification Code")
        WECHAT = 2, _("Wechat Scan Code")

    status = models.BooleanField(default=True, verbose_name=_("Login Status"))
    ipaddress = models.GenericIPAddressField(verbose_name=_("IpAddress"), null=True, blank=True)
    browser = models.CharField(max_length=64, verbose_name=_("Browser"), null=True, blank=True)
    system = models.CharField(max_length=64, verbose_name=_("System"), null=True, blank=True)
    agent = models.CharField(max_length=128, verbose_name=_("Agent"), null=True, blank=True)
    login_type = models.SmallIntegerField(default=LoginTypeChoices.USERNAME, choices=LoginTypeChoices,
                                          verbose_name=_("Login Type"))

    class Meta:
        verbose_name = _("User Login Log")
        verbose_name_plural = verbose_name


class OperationLog(DbAuditModel):
    module = models.CharField(max_length=64, verbose_name=_("Module"), null=True, blank=True)
    path = models.CharField(max_length=400, verbose_name=_("URL Path"), null=True, blank=True)
    body = models.TextField(verbose_name=_("Request Body"), null=True, blank=True)
    method = models.CharField(max_length=8, verbose_name=_("Request Method"), null=True, blank=True)
    ipaddress = models.GenericIPAddressField(verbose_name=_("IpAddress"), null=True, blank=True)
    browser = models.CharField(max_length=64, verbose_name=_("Browser"), null=True, blank=True)
    system = models.CharField(max_length=64, verbose_name=_("System"), null=True, blank=True)
    response_code = models.IntegerField(verbose_name=_("Response Code"), null=True, blank=True)
    response_result = models.TextField(verbose_name=_("Response Result"), null=True, blank=True)
    status_code = models.IntegerField(verbose_name=_("Status Code"), null=True, blank=True)

    class Meta:
        verbose_name = _("Operation Log")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def remove_expired(cls, clean_day=30 * 6):
        clean_time = timezone.now() - datetime.timedelta(days=clean_day)
        cls.objects.filter(created_time__lt=clean_time).delete()

    remove_expired = classmethod(remove_expired)


class UploadFile(DbAuditModel):
    filepath = models.FileField(verbose_name=_("FilePath"), null=True, blank=True, upload_to=upload_directory_path)
    file_url = models.URLField(verbose_name=_("Internet URL"), max_length=255, blank=True, null=True,
                               help_text=_("Usually an address accessible to the outside Internet"))
    filename = models.CharField(verbose_name=_("Filename"), max_length=255)
    filesize = models.IntegerField(verbose_name=_("Filesize"))
    mime_type = models.CharField(max_length=255, verbose_name=_("Mime Type"))
    md5sum = models.CharField(max_length=36, verbose_name=_("File Md5sum"))
    is_tmp = models.BooleanField(verbose_name=_("Tmp File"), default=False,
                                 help_text=_("Temporary files are automatically cleared by scheduled tasks"))
    is_upload = models.BooleanField(verbose_name=_("Upload File"), default=False)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        self.filename = self.filename[:255]
        if not self.md5sum and not self.file_url:
            md5 = hashlib.md5()
            for chunk in self.filepath.chunks():
                md5.update(chunk)
            if not self.filesize:
                self.filesize = self.filepath.size
            self.md5sum = md5.hexdigest()
        return super().save(force_insert, force_update, using, update_fields)

    class Meta:
        verbose_name = _("Upload File")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.filename}"


class NoticeMessage(DbAuditModel):
    class NoticeChoices(models.IntegerChoices):
        SYSTEM = 0, _("System Notification")
        NOTICE = 1, _("System Announcement")
        USER = 2, _("User Notification")
        DEPT = 3, _("Department Notification")
        ROLE = 4, _("Role Notification")

    class LevelChoices(models.TextChoices):
        DEFAULT = 'info', _("Ordinary Notices")
        PRIMARY = 'primary', _("General Notices")
        SUCCESS = 'success', _("Success Notices")
        DANGER = 'danger', _("Important Notices")

    notice_user = models.ManyToManyField(to=UserInfo, through="NoticeUserRead", null=True, blank=True,
                                         through_fields=('notice', 'owner'), verbose_name=_("The Notified User"))
    notice_dept = models.ManyToManyField(to=DeptInfo, null=True, blank=True, verbose_name=_("The Notified Department"))
    notice_role = models.ManyToManyField(to=UserRole, null=True, blank=True, verbose_name=_("The Notified Role"))
    level = models.CharField(verbose_name=_("Notice Level"), choices=LevelChoices, default=LevelChoices.DEFAULT,
                             max_length=20)
    notice_type = models.SmallIntegerField(verbose_name=_("Notice Type"), choices=NoticeChoices,
                                           default=NoticeChoices.USER)
    title = models.CharField(verbose_name=_("Notice Title"), max_length=255)
    message = models.TextField(verbose_name=_("Notice Message"), blank=True, null=True)
    extra_json = models.JSONField(verbose_name=_("Additional Json Data"), blank=True, null=True)
    file = models.ManyToManyField(to=UploadFile, verbose_name=_("Uploaded Attachments"))
    publish = models.BooleanField(verbose_name=_("Publish"), default=True)

    @classmethod
    @property
    def user_choices(cls):
        return [cls.NoticeChoices.USER, cls.NoticeChoices.SYSTEM]

    @classmethod
    @property
    def notice_choices(cls):
        return [cls.NoticeChoices.NOTICE, cls.NoticeChoices.DEPT, cls.NoticeChoices.ROLE]

    class Meta:
        verbose_name = _("Notice Message")
        verbose_name_plural = verbose_name
        ordering = ('-created_time',)

    def delete(self, using=None, keep_parents=False):
        if self.file:
            for file in self.file.all():
                file.delete()
        return super().delete(using, keep_parents)

    def __str__(self):
        return f"{self.title}-{self.get_notice_type_display()}"


class NoticeUserRead(DbAuditModel):
    owner = models.ForeignKey(to=UserInfo, on_delete=models.CASCADE, verbose_name=_("User"))
    notice = models.ForeignKey(NoticeMessage, on_delete=models.CASCADE, verbose_name=_("Notice"))
    unread = models.BooleanField(verbose_name=_("Unread"), default=True, blank=False, db_index=True)

    class Meta:
        ordering = ('-created_time',)
        verbose_name = _("User has read the message")
        verbose_name_plural = verbose_name
        index_together = ('owner', 'unread')
        unique_together = ('owner', 'notice')


class BaseConfig(DbAuditModel):
    value = models.TextField(max_length=10240, verbose_name=_("Config Value"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    access = models.BooleanField(default=False, verbose_name=_("API Access"),
                                 help_text=_("Allows API interfaces to access this config"))

    class Meta:
        abstract = True


class SystemConfig(BaseConfig, DbUuidModel):
    key = models.CharField(max_length=255, unique=True, verbose_name=_("Config Name"))
    inherit = models.BooleanField(default=False, verbose_name=_("User Inherit"),
                                  help_text=_("Allows users to inherit this config"))

    class Meta:
        verbose_name = _("System Config")
        verbose_name_plural = verbose_name

    def __str__(self):
        return "%s-%s" % (self.key, self.description)


class UserPersonalConfig(BaseConfig):
    owner = models.ForeignKey(to=UserInfo, verbose_name=_("User"), on_delete=models.CASCADE)
    key = models.CharField(max_length=255, verbose_name=_("Config Name"))

    class Meta:
        verbose_name = _("User Config")
        verbose_name_plural = verbose_name
        unique_together = (('owner', 'key'),)

    def __str__(self):
        return "%s-%s" % (self.key, self.description)
