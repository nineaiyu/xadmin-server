#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : menu
# author : ly_13
# date : 8/10/2024

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.models import SoftDeleteModel, DbAuditModel, DbUuidModel


class MenuMeta(DbAuditModel, DbUuidModel):
    title = models.CharField(verbose_name=_("Menu title"), max_length=255, null=True, blank=True)
    icon = models.CharField(verbose_name=_("Left icon"), max_length=255, null=True, blank=True)
    r_svg_name = models.CharField(verbose_name=_("Right icon"), max_length=255, null=True, blank=True,
                                  help_text=_("Additional icon to the right of menu name"))
    is_show_menu = models.BooleanField(verbose_name=_("Show menu"), default=True)
    is_show_parent = models.BooleanField(verbose_name=_("Show parent menu"), default=False)
    is_keepalive = models.BooleanField(verbose_name=_("Keepalive"), default=True,
                                       help_text=_(
                                           "When enabled, the entire state of the page is saved, and when refreshed, the state is cleared"))
    frame_url = models.CharField(verbose_name=_("Iframe URL"), max_length=255, null=True, blank=True,
                                 help_text=_("The embedded iframe link address"))
    frame_loading = models.BooleanField(verbose_name=_("Iframe loading"), default=False)

    transition_enter = models.CharField(verbose_name=_("Enter animation"), max_length=255, null=True, blank=True)
    transition_leave = models.CharField(verbose_name=_("Leave animation"), max_length=255, null=True, blank=True)

    is_hidden_tag = models.BooleanField(verbose_name=_("Hidden tag"), default=False, help_text=_(
        "The current menu name or custom information is prohibited from being added to the TAB"))
    fixed_tag = models.BooleanField(verbose_name=_("Fixed tag"), default=False, help_text=_(
        "Whether the current menu name is fixed to the TAB and cannot be closed"))
    dynamic_level = models.IntegerField(verbose_name=_("Dynamic level"), default=0,
                                        help_text=_("Maximum number of dynamic routes that can be opened"))

    class Meta:
        verbose_name = _("Menu meta")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.title}-{self.description}"


class Menu(SoftDeleteModel, DbAuditModel, DbUuidModel):
    """
    菜单软删除——删除进入回收站可恢复；
    目录删除会级联标记全部后代菜单（同一时间戳，恢复/清除时成组处理）。
    """

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

    parent = models.ForeignKey('system.Menu', on_delete=models.SET_NULL, verbose_name=_("Parent menu"), null=True,
                               blank=True)
    menu_type = models.SmallIntegerField(choices=MenuChoices, default=MenuChoices.DIRECTORY,
                                         verbose_name=_("Menu type"))
    # unique=True 降级为"未删除数据"条件约束（见 Meta.constraints），
    # 已删除菜单释放组件名/权限码，可被新菜单复用
    name = models.CharField(verbose_name=_("Component name or permission code"), max_length=128)
    rank = models.IntegerField(verbose_name=_("Rank"), default=9999)
    path = models.CharField(verbose_name=_("Route path or api path"), max_length=255)
    component = models.CharField(verbose_name=_("Component path"), max_length=255, null=True, blank=True)
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)
    meta = models.OneToOneField("system.MenuMeta", on_delete=models.CASCADE, verbose_name=_("Menu meta"))
    model = models.ManyToManyField("system.ModelLabelField", verbose_name=_("Model"), blank=True)

    # permission_marking = models.CharField(verbose_name="权限标识", max_length=255)
    # api_route = models.CharField(verbose_name="后端权限路由", max_length=255, null=True, blank=True)
    method = models.CharField(choices=MethodChoices, null=True, blank=True, verbose_name=_("Method"), max_length=10)

    # api_auth_access = models.BooleanField(verbose_name="是否授权访问，否的话可以匿名访问后端路由", default=True)

    def delete(self, *args, **kwargs):
        """软删除：标记自身并级联标记全部后代菜单（同一时间戳，成组恢复/清除）。"""
        deleted_at = timezone.now()
        self.deleted_at = deleted_at
        self.save(update_fields=['deleted_at'])
        self._cascade_soft_delete_descendants(deleted_at)
        return 1

    def hard_delete(self, *args, **kwargs):
        """物理删除：meta 以 CASCADE 指向本模型，删除 meta 即级联删除菜单行
        （沿用原 delete() 的清理顺序），随后清理残余。"""
        if self.meta_id:
            MenuMeta.objects.filter(pk=self.meta_id).delete()
        return super().hard_delete(*args, **kwargs)

    def _cascade_soft_delete_descendants(self, deleted_at):
        """按广度优先把未删除的后代菜单标记为同一 deleted_at。"""
        frontier = [self.pk]
        while frontier:
            children = list(Menu.objects.filter(parent_id__in=frontier).values_list('pk', flat=True))
            if not children:
                break
            Menu.objects.filter(pk__in=children, deleted_at__isnull=True).update(deleted_at=deleted_at)
            frontier = children

    def get_deleted_descendants(self):
        """与本菜单同一时间戳软删除的后代菜单（成组恢复/清除的口径）。"""
        if self.deleted_at is None:
            return Menu.objects.none()
        return Menu.all_objects.filter(deleted_at=self.deleted_at).exclude(pk=self.pk)

    class Meta:
        verbose_name = _("Menu")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(deleted_at__isnull=True),
                                    name='uniq_menu_name_active'),
        ]

    def __str__(self):
        # meta 可能已被级联删除（purge 物理清除流程），缓存访问会抛 KeyError
        try:
            title = self.meta.title
        except (ObjectDoesNotExist, KeyError, AttributeError):
            title = None
        return f"{title}-{self.get_menu_type_display()}({self.name})"
