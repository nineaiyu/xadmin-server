import datetime
import time

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


# Create your models here.
class DbBaseModel(models.Model):
    created_time = models.DateTimeField(auto_now_add=True, verbose_name="添加时间")
    updated_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    description = models.CharField(max_length=128, verbose_name="描述信息", null=True, blank=True)

    class Meta:
        abstract = True


class MenuMeta(DbBaseModel):
    title = models.CharField(verbose_name="菜单名称", max_length=256, null=True, blank=True)
    icon = models.CharField(verbose_name="菜单图标", max_length=256, null=True, blank=True)
    r_svg_name = models.CharField(verbose_name="菜单右侧额外图标iconfont名称，目前只支持iconfont", max_length=256,
                                  null=True, blank=True)
    is_show_menu = models.BooleanField(verbose_name="是否显示该菜单", default=True)
    is_show_parent = models.BooleanField(verbose_name="是否显示父级菜单", default=False)
    is_keepalive = models.BooleanField(verbose_name="是否开启页面缓存", default=False,
                                       help_text='开启后，会保存该页面的整体状态，刷新后会清空状态')
    frame_url = models.CharField(verbose_name="内嵌的iframe链接地址", max_length=256, null=True, blank=True)
    frame_loading = models.BooleanField(verbose_name="内嵌的iframe页面是否开启首次加载动画", default=False)

    transition_enter = models.CharField(verbose_name="当前页面进场动画", max_length=256, null=True, blank=True)
    transition_leave = models.CharField(verbose_name="当前页面离场动画", max_length=256, null=True, blank=True)

    is_hidden_tag = models.BooleanField(verbose_name="当前菜单名称或自定义信息禁止添加到标签页", default=False)
    dynamic_level = models.IntegerField(verbose_name="显示标签页最大数量", default=1)

    class Meta:
        verbose_name = "菜单元数据"
        verbose_name_plural = "菜单元数据"
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.title}-{self.description}"


class Menu(DbBaseModel):
    parent = models.ForeignKey(to='Menu', on_delete=models.SET_NULL, verbose_name="父节点", null=True, blank=True)

    menu_type_choices = ((0, '目录'), (1, '菜单'), (2, '权限'))
    menu_type = models.SmallIntegerField(choices=menu_type_choices, default=0, verbose_name="节点类型")

    name = models.CharField(verbose_name="组件英文名称", max_length=128, unique=True)
    rank = models.IntegerField(verbose_name="菜单顺序", default=9999)
    path = models.CharField(verbose_name="路由地址", max_length=256)
    component = models.CharField(verbose_name="组件地址", max_length=256, null=True, blank=True)
    is_active = models.BooleanField(verbose_name="是否启用该菜单", default=True)
    meta = models.OneToOneField(to=MenuMeta, on_delete=models.CASCADE, verbose_name="菜单元数据")

    # permission_marking = models.CharField(verbose_name="权限标识", max_length=256)
    # api_route = models.CharField(max_length=256, verbose_name="后端权限路由")
    method_choices = (('GET', 'get'), ('POST', 'post'), ('PUT', 'put'), ('DELETE', 'delete'))

    # method = models.CharField(choices=method_choices, default='GET', verbose_name="请求方式", max_length=10)

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


# class Permission(DbBaseModel):
#     name = models.CharField(verbose_name="权限名称", max_length=256)
#     rank = models.IntegerField(verbose_name="权限顺序", default=0)
#     menu = models.ForeignKey(to=Menu, on_delete=models.CASCADE, verbose_name="所属菜单")
#     permission_marking = models.CharField(verbose_name="权限标识", max_length=256)
#
#     api_route = models.CharField(max_length=256, verbose_name="后端权限路由")
#     method_choices = (('GET', 'get'), ('POST', 'post'), ('PUT', 'put'), ('DELETE', 'delete'))
#     method = models.CharField(choices=method_choices, default='GET', verbose_name="请求方式", max_length=10)
#
#     class Meta:
#         verbose_name = "菜单标签权限"
#         verbose_name_plural = "菜单标签权限"
#
#     def __str__(self):
#         return f"{self.name}-{self.created_time}"


class UserRole(DbBaseModel):
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


def upload_directory_path(instance, filename):
    # file will be uploaded to MEDIA_ROOT/user_<id>/<filename>
    prefix = filename.split('.')[-1]
    new_filename = str(time.time()).replace('.', '')
    return time.strftime(f"{instance.pk}/%Y/%m/%d/%S/{new_filename}.{prefix}")


class UserInfo(AbstractUser):
    roles = models.ManyToManyField(to="UserRole", verbose_name="角色", blank=True, null=True)
    avatar = models.FileField(verbose_name="用户头像", null=True, blank=True, upload_to=upload_directory_path)
    nickname = models.CharField(verbose_name="昵称", max_length=150, blank=True)
    sex = models.SmallIntegerField(verbose_name="性别", default=0, help_text='0：男 1：女 2：保密')
    mobile = models.CharField(verbose_name="手机号", max_length=16, default='')

    class Meta:
        verbose_name = "用户信息"
        verbose_name_plural = "用户信息"
        ordering = ("-date_joined",)

    def delete(self, using=None, keep_parents=False):
        if self.avatar:
            self.avatar.delete()  # 删除存储的头像文件
        super().delete(using, keep_parents)

    def __str__(self):
        return f"{self.username}-{self.roles}"


class OperationLog(DbBaseModel):
    owner = models.ForeignKey(to=UserInfo, related_query_name='creator_query', null=True, blank=True,
                              verbose_name='创建人', on_delete=models.SET_NULL)
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
