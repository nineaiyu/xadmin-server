import datetime

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from pilkit.processors import ResizeToFill

from common.core.models import upload_directory_path, DbAuditModel, DbUuidModel, DbCharModel
from common.fields.image import ProcessedImageField


class ModelLabelField(DbAuditModel, DbUuidModel):
    class KeyChoices(models.TextChoices):
        TEXT = 'value.text', _('文本格式')
        JSON = 'value.json', _('json格式')
        ALL = 'value.all', _('全部数据')
        DATETIME = 'value.datetime', _('日期时间')
        DATETIME_RANGE = 'value.datetime.range', _('日期时间区间选择器')
        DATE = 'value.date', _('距离当前时间多少秒')
        OWNER = 'value.user.id', _('本人ID')
        OWNER_DEPARTMENT = 'value.user.dept.id', _('本部门ID')
        OWNER_DEPARTMENTS = 'value.user.dept.ids', _('本部门ID及部门以下数据')
        DEPARTMENTS = 'value.dept.ids', _('部门ID及部门以下数据')
        TABLE_USER = 'value.table.user.ids', _('选择用户ID')
        TABLE_MENU = 'value.table.menu.ids', _('选择菜单ID')
        TABLE_ROLE = 'value.table.role.ids', _('选择角色ID')
        TABLE_DEPT = 'value.table.dept.ids', _('选择部门ID')

    class FieldChoices(models.IntegerChoices):
        ROLE = 0, _("角色权限")
        DATA = 1, _("数据权限")

    field_type = models.SmallIntegerField(choices=FieldChoices, default=FieldChoices.DATA, verbose_name="字段类型")
    parent = models.ForeignKey(to='ModelLabelField', on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(verbose_name="模型/字段数值", max_length=128)
    label = models.CharField(verbose_name="模型/字段名称", max_length=128)

    class Meta:
        unique_together = ('name', 'parent')
        verbose_name = "模型字段"
        verbose_name_plural = "模型字段"

    def __str__(self):
        return f"{self.name} {self.label}"


class ModeTypeAbstract(models.Model):
    class ModeChoices(models.IntegerChoices):
        OR = 0, _("或模式")
        AND = 1, _("且模式")

    mode_type = models.SmallIntegerField(choices=ModeChoices, default=ModeChoices.OR, verbose_name="数据权限模式")

    class Meta:
        abstract = True


class UserInfo(DbAuditModel, AbstractUser, ModeTypeAbstract):
    class GenderChoices(models.IntegerChoices):
        UNKNOWN = 0, _("保密")
        MALE = 1, _("男")
        FEMALE = 2, _("女")

    avatar = ProcessedImageField(verbose_name="用户头像", null=True, blank=True,
                                 upload_to=upload_directory_path,
                                 processors=[ResizeToFill(512, 512)],  # 默认存储像素大小
                                 scales=[1, 2, 3, 4],  # 缩略图可缩小倍数，
                                 format='png')

    nickname = models.CharField(verbose_name="昵称", max_length=150, blank=True)
    gender = models.IntegerField(choices=GenderChoices, default=GenderChoices.UNKNOWN, verbose_name="性别")
    mobile = models.CharField(verbose_name="手机号", max_length=16, default='', blank=True)

    roles = models.ManyToManyField(to="UserRole", verbose_name="角色", blank=True, null=True)
    rules = models.ManyToManyField(to="DataPermission", verbose_name="数据权限", blank=True, null=True)
    dept = models.ForeignKey(to="DeptInfo", verbose_name="所属部门", on_delete=models.PROTECT, blank=True, null=True,
                             related_query_name="dept_query")

    class Meta:
        verbose_name = "用户信息"
        verbose_name_plural = "用户信息"
        ordering = ("-date_joined",)

    def delete(self, using=None, keep_parents=False):
        if self.avatar:
            self.avatar.delete()  # 删除存储的头像文件
        return super().delete(using, keep_parents)

    def __str__(self):
        return f"{self.username}"


class MenuMeta(DbAuditModel, DbUuidModel):
    title = models.CharField(verbose_name="菜单名称", max_length=256, null=True, blank=True)
    icon = models.CharField(verbose_name="菜单图标", max_length=256, null=True, blank=True)
    r_svg_name = models.CharField(verbose_name="菜单右侧额外图标", max_length=256, null=True, blank=True,
                                  help_text='菜单右侧额外图标iconfont名称，目前只支持iconfont')
    is_show_menu = models.BooleanField(verbose_name="是否显示该菜单", default=True)
    is_show_parent = models.BooleanField(verbose_name="是否显示父级菜单", default=False)
    is_keepalive = models.BooleanField(verbose_name="是否开启页面缓存", default=False,
                                       help_text='开启后，会保存该页面的整体状态，刷新后会清空状态')
    frame_url = models.CharField(verbose_name="内嵌的iframe链接地址", max_length=256, null=True, blank=True)
    frame_loading = models.BooleanField(verbose_name="内嵌的iframe页面是否开启首次加载动画", default=False)

    transition_enter = models.CharField(verbose_name="当前页面进场动画", max_length=256, null=True, blank=True)
    transition_leave = models.CharField(verbose_name="当前页面离场动画", max_length=256, null=True, blank=True)

    is_hidden_tag = models.BooleanField(verbose_name="当前菜单名称或自定义信息禁止添加到标签页", default=False)
    fixed_tag = models.BooleanField(verbose_name="当前菜单名称是否固定显示在标签页且不可关闭", default=False)
    dynamic_level = models.IntegerField(verbose_name="显示标签页最大数量", default=1)

    class Meta:
        verbose_name = "菜单元数据"
        verbose_name_plural = "菜单元数据"
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.title}-{self.description}"


class Menu(DbAuditModel, DbUuidModel):
    class MenuChoices(models.IntegerChoices):
        DIRECTORY = 0, _("目录")
        MENU = 1, _("菜单")
        PERMISSION = 2, _("权限")

    class MethodChoices(models.TextChoices):
        GET = 'GET', _("GET")
        POST = 'POST', _("POST")
        PUT = 'PUT', _("PUT")
        DELETE = 'DELETE', _("DELETE")
        PATCH = 'PATCH', _("PATCH")

    parent = models.ForeignKey(to='Menu', on_delete=models.SET_NULL, verbose_name="父节点", null=True, blank=True)
    menu_type = models.SmallIntegerField(choices=MenuChoices, default=MenuChoices.DIRECTORY, verbose_name="节点类型")
    name = models.CharField(verbose_name="组件英文名称 或 权限标识", max_length=128, unique=True)
    rank = models.IntegerField(verbose_name="菜单顺序", default=9999)
    path = models.CharField(verbose_name="路由地址 或 后端权限路由", max_length=256)
    component = models.CharField(verbose_name="组件地址", max_length=256, null=True, blank=True)
    is_active = models.BooleanField(verbose_name="是否启用该菜单", default=True)
    meta = models.OneToOneField(to=MenuMeta, on_delete=models.CASCADE, verbose_name="菜单元数据")
    model = models.ManyToManyField(to=ModelLabelField, verbose_name="绑定模型", null=True, blank=True)

    # permission_marking = models.CharField(verbose_name="权限标识", max_length=256)
    # api_route = models.CharField(verbose_name="后端权限路由", max_length=256, null=True, blank=True)
    method = models.CharField(choices=MethodChoices, null=True, blank=True, verbose_name="请求方式", max_length=10)

    # api_auth_access = models.BooleanField(verbose_name="是否授权访问，否的话可以匿名访问后端路由", default=True)

    def delete(self, using=None, keep_parents=False):
        if self.meta:
            self.meta.delete(using, keep_parents)
        super().delete(using, keep_parents)

    class Meta:
        verbose_name = "菜单信息"
        verbose_name_plural = "菜单信息"
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}-{self.menu_type}-{self.meta.title}"


class DataPermission(DbAuditModel, ModeTypeAbstract, DbUuidModel):
    name = models.CharField(verbose_name="数据权限名称", max_length=256, unique=True)
    rules = models.JSONField(verbose_name="规则", max_length=512, default=list)
    is_active = models.BooleanField(verbose_name="是否启用", default=True)
    menu = models.ManyToManyField(to=Menu, verbose_name="权限菜单", null=True, blank=True)

    class Meta:
        verbose_name = "数据权限"
        verbose_name_plural = "数据权限"

    def __str__(self):
        return f"{self.name}-{self.is_active}"


class UserRole(DbAuditModel, DbUuidModel):
    name = models.CharField(max_length=128, verbose_name="角色名称", unique=True)
    code = models.CharField(max_length=128, verbose_name="角色标识", unique=True)
    is_active = models.BooleanField(verbose_name="是否启用", default=True)
    menu = models.ManyToManyField(to='Menu', verbose_name="菜单权限", null=True, blank=True)

    class Meta:
        verbose_name = "角色信息"
        verbose_name_plural = "角色信息"
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}-{self.created_time}"


class FieldPermission(DbAuditModel, DbCharModel):
    role = models.ForeignKey(UserRole, on_delete=models.CASCADE, verbose_name="角色")
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, verbose_name="菜单")
    field = models.ManyToManyField(ModelLabelField, verbose_name="字段", null=True, blank=True)

    class Meta:
        verbose_name = "字段权限表"
        verbose_name_plural = "字段权限表"
        ordering = ("-created_time",)
        unique_together = ("role", "menu")

    def save(self, *args, **kwargs):
        self.id = f"{self.role.pk}-{self.menu.pk}"
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.pk}-{self.role.name}-{self.created_time}"


class DeptInfo(DbAuditModel, ModeTypeAbstract, DbUuidModel):
    name = models.CharField(verbose_name="部门名称", max_length=128)
    code = models.CharField(max_length=128, verbose_name="部门标识", unique=True)
    parent = models.ForeignKey(to='DeptInfo', on_delete=models.SET_NULL, verbose_name="父节点", null=True, blank=True,
                               related_query_name="parent_query")
    roles = models.ManyToManyField(to="UserRole", verbose_name="角色", blank=True, null=True)
    rules = models.ManyToManyField(to="DataPermission", verbose_name="数据权限", blank=True, null=True)
    rank = models.IntegerField(verbose_name="顺序", default=99)
    auto_bind = models.BooleanField(verbose_name="是否绑定该部门", default=False)
    is_active = models.BooleanField(verbose_name="是否启用", default=True)

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
        return list(set(dept_list))

    class Meta:
        verbose_name = "部门信息"
        verbose_name_plural = "部门信息"
        ordering = ("-rank", "-created_time",)


class UserLoginLog(DbAuditModel):
    class LoginTypeChoices(models.IntegerChoices):
        USERNAME = 0, _("用户密码登录")
        SMS = 1, _("短信验证登录")
        WECHAT = 2, _("微信扫码登录")

    status = models.BooleanField(default=True, verbose_name="是否登录成功")
    ipaddress = models.GenericIPAddressField(verbose_name="登录ip地址", null=True, blank=True)
    browser = models.CharField(max_length=64, verbose_name="登录浏览器", null=True, blank=True)
    system = models.CharField(max_length=64, verbose_name="操作系统", null=True, blank=True)
    agent = models.CharField(max_length=128, verbose_name="agent信息", null=True, blank=True)
    login_type = models.SmallIntegerField(default=LoginTypeChoices.USERNAME, choices=LoginTypeChoices,
                                          verbose_name="登录类型")

    class Meta:
        verbose_name = "登录日志"
        verbose_name_plural = "登录日志"


class OperationLog(DbAuditModel):
    module = models.CharField(max_length=64, verbose_name="请求模块", null=True, blank=True)
    path = models.CharField(max_length=400, verbose_name="请求地址", null=True, blank=True)
    body = models.TextField(verbose_name="请求参数", null=True, blank=True)
    method = models.CharField(max_length=8, verbose_name="请求方式", null=True, blank=True)
    ipaddress = models.GenericIPAddressField(verbose_name="请求ip地址", null=True, blank=True)
    browser = models.CharField(max_length=64, verbose_name="请求浏览器", null=True, blank=True)
    system = models.CharField(max_length=64, verbose_name="请求操作系统", null=True, blank=True)
    response_code = models.IntegerField(verbose_name="响应状态码", null=True, blank=True)
    response_result = models.TextField(verbose_name="响应数据", null=True, blank=True)
    status_code = models.IntegerField(verbose_name="请求状态码", null=True, blank=True)

    class Meta:
        verbose_name = "操作日志"
        verbose_name_plural = "操作日志"
        ordering = ("-created_time",)

    def remove_expired(cls, clean_day=30 * 6):
        clean_time = timezone.now() - datetime.timedelta(days=clean_day)
        cls.objects.filter(created_time__lt=clean_time).delete()

    remove_expired = classmethod(remove_expired)


class UploadFile(DbAuditModel):
    filepath = models.FileField(verbose_name="文件存储", null=True, blank=True, upload_to=upload_directory_path)
    filename = models.CharField(verbose_name="文件原始名称", max_length=150)
    filesize = models.IntegerField(verbose_name="文件大小")
    is_tmp = models.BooleanField(verbose_name="是否临时文件", default=True)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        self.filename = self.filename[:120]
        return super().save(force_insert, force_update, using, update_fields)

    def delete(self, using=None, keep_parents=False):
        if self.filepath:
            self.filepath.delete()  # 删除存储的文件
        return super().delete(using, keep_parents)

    class Meta:
        verbose_name = "上传的文件"
        verbose_name_plural = "上传的文件"


class NoticeMessage(DbAuditModel):
    class NoticeChoices(models.IntegerChoices):
        SYSTEM = 0, _("系统通知")
        NOTICE = 1, _("系统公告")
        USER = 2, _("用户通知")
        DEPT = 3, _("部门通知")
        ROLE = 4, _("角色通知")

    class LevelChoices(models.TextChoices):
        DEFAULT = 'info', _("普通通知")
        PRIMARY = 'primary', _("一般通知")
        SUCCESS = 'success', _("成功通知")
        DANGER = 'danger', _("重要通知")

    notice_user = models.ManyToManyField(to=UserInfo, through="NoticeUserRead", null=True, blank=True,
                                         through_fields=('notice', 'owner'), verbose_name="通知的人")
    notice_dept = models.ManyToManyField(to=DeptInfo, null=True, blank=True, verbose_name="通知的人部门")
    notice_role = models.ManyToManyField(to=UserRole, null=True, blank=True, verbose_name="通知的人角色")
    level = models.CharField(verbose_name='消息级别', choices=LevelChoices, default=LevelChoices.DEFAULT,
                             max_length=20)
    notice_type = models.SmallIntegerField(verbose_name="消息类型", choices=NoticeChoices, default=NoticeChoices.USER)
    title = models.CharField(verbose_name='消息标题', max_length=255)
    message = models.TextField(verbose_name='具体信息内容', blank=True, null=True)
    extra_json = models.JSONField(verbose_name="额外的json数据", blank=True, null=True)
    file = models.ManyToManyField(to=UploadFile, verbose_name="上传的资源")
    publish = models.BooleanField(verbose_name="是否发布", default=True)

    @classmethod
    @property
    def user_choices(cls):
        return [cls.NoticeChoices.USER, cls.NoticeChoices.SYSTEM]

    @classmethod
    @property
    def notice_choices(cls):
        return [cls.NoticeChoices.NOTICE, cls.NoticeChoices.DEPT, cls.NoticeChoices.ROLE]

    class Meta:
        verbose_name = "消息通知"
        verbose_name_plural = "消息通知"
        ordering = ('-created_time',)

    def delete(self, using=None, keep_parents=False):
        if self.file:
            for file in self.file.all():
                file.delete()
        return super().delete(using, keep_parents)

    def __str__(self):
        return f"{self.title}-{self.created_time}-{self.get_notice_type_display()}"


class NoticeUserRead(DbAuditModel):
    owner = models.ForeignKey(to=UserInfo, on_delete=models.CASCADE)
    notice = models.ForeignKey(NoticeMessage, on_delete=models.CASCADE)
    unread = models.BooleanField(verbose_name='是否未读', default=True, blank=False, db_index=True)

    class Meta:
        ordering = ('-created_time',)
        verbose_name = "用户已读消息"
        verbose_name_plural = "用户已读消息"
        index_together = ('owner', 'unread')
        unique_together = ('owner', 'notice')


class BaseConfig(DbAuditModel):
    value = models.TextField(max_length=10240, verbose_name="配置值")
    is_active = models.BooleanField(default=True, verbose_name="是否启用该配置项")
    access = models.BooleanField(default=False, verbose_name="允许api访问该配置")

    class Meta:
        verbose_name = '基础配置'
        verbose_name_plural = "基础配置"
        abstract = True


class SystemConfig(BaseConfig, DbUuidModel):
    key = models.CharField(max_length=256, unique=True, verbose_name="配置名称")
    inherit = models.BooleanField(default=False, verbose_name="允许用户继承该配置")

    class Meta:
        verbose_name = '系统配置项'
        verbose_name_plural = "系统配置项"

    def __str__(self):
        return "%s-%s" % (self.key, self.description)


class UserPersonalConfig(BaseConfig):
    owner = models.ForeignKey(to=UserInfo, verbose_name="用户ID", on_delete=models.CASCADE)
    key = models.CharField(max_length=256, verbose_name="配置名称")

    class Meta:
        verbose_name = '个人配置项'
        verbose_name_plural = "个人配置项"
        unique_together = (('owner', 'key'),)

    def __str__(self):
        return "%s-%s" % (self.key, self.description)
