#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializer
# author : ly_13
# date : 6/12/2024

from rest_framework import serializers

from common.core.serializers import BaseModelSerializer, TabsColumn
from common.fields.utils import input_wrapper
from demo import models


class BookSerializer(BaseModelSerializer):
    class Meta:
        model = models.Book
        ## pk 字段用于前端删除，更新等标识，如果有删除更新等，必须得加上 pk 字段
        ## 数据返回的字段，该字段受字段权限控制

        ############### 1.使用简易 tabs 表单 #############
        tabs = [
            TabsColumn(
                "基本信息",
                [
                    "name",
                    "isbn",
                    "category",
                    "status",
                    "is_active",
                    "author",
                    "publisher",
                    "publication_date",
                    "price",
                    "on_shelf_time",
                    "created_time",
                    "updated_time",
                ],
            ),
            TabsColumn("管理员", ["admin", "admin2", "managers", "managers2"]),
            TabsColumn("文件信息", ["avatar", "cover", "book_file", "file", "files"]),
        ]
        # deleted_at 随接口返回，供回收站抽屉展示删除时间（软删除模型专用）
        fields = ["pk", "block", "deleted_at"]
        ########### 单表单结束 ################

        ############### 2.默认的单表单 ##############
        # fields = [
        #     'pk', 'name', 'isbn', 'category', 'is_active', 'author', 'publisher', 'publication_date', 'price', 'block',
        #     'created_time', 'admin', 'admin2', 'managers', 'managers2', 'avatar', 'cover', 'book_file', 'file', 'files',
        #     'updated_time',
        # ]
        ########### 单表单结束 ################

        ## 仅用于前端table表格字段有顺序的展示，如果没定义，默认使用 fields 定义的变量
        ## 为啥要有这个变量？ 一般情况下，前端table表格宽度不够，不需要显示太多字段，就可以通过这个变量来控制显示的字段
        table_fields = [
            "pk",
            "cover",
            "category",
            "status",
            "name",
            "is_active",
            "isbn",
            "author",
            "publisher",
            "publication_date",
            "price",
            "on_shelf_time",
            "book_file",
            "file",
            "files",
        ]

        # fields_unexport = ['pk']  # 导入导出文件时，忽略该字段

        # read_only_fields = ['pk']  # 表示pk字段只读, 和 extra_kwargs 定义的 pk 含义一样

        ## 构建字段的额外参数
        # # extra_kwargs包含了admin 单对多的两种方式，managers 多对多的两种方式，区别在于自定义的input_type，
        # # 观察前端页面变化和 search-columns 请求的数据
        extra_kwargs = {
            "pk": {"read_only": True},  # 表示pk字段只读
            # 上架状态由「提交上架」审批流驱动（终态经信号回写），接口层只读
            "status": {"read_only": True},
            "admin": {
                "attrs": ["pk", "username"],
                "required": True,
                "format": "{username}({pk})",
                "input_type": "api-search-user",
            },
            "admin2": {
                "attrs": ["pk", "username"],
                "required": True,
                "format": "{username}({pk})",
            },
            "managers": {
                "attrs": ["pk", "username"],
                "required": True,
                "format": "{username}({pk})",
                "input_type": "api-search-user",
            },
            "managers2": {
                "attrs": ["pk", "username"],
                "required": False,
                "format": "{username}({pk})",
            },
            # 多文件关联默认的 input_type为 m2m_related_field_file
            "files": {
                "attrs": ["pk", "filepath", "filesize", "filename"],
                "required": False,
                "format": "{filename}({pk})",
                "ignore_field_permission": True,
            },
            # 单文件关联默认的 input_type为 object_related_field_file  ，为了让前端支持图片上传后回显，需要添加 'input_type_suffix': 'image'
            # ignore_field_permission 忽略上传文件的字段控制权限
            "file": {
                "attrs": ["pk", "filepath", "filesize", "filename"],
                "required": False,  # 可选附件（与模型层 blank=True/null=True 同口径）
                "format": "{filename}({pk})",
                "ignore_field_permission": True,
                "input_type_suffix": "image",
            },
        }

    # # 该方法定义了管理字段，和 extra_kwargs 定义的 admin 含义一样，该字段会被序列化为
    # # 定义带有关联关系的字段，比如上面的admin，则额外参数中，input_type 和 attrs 至少存在一个，要不然前端可能会解析失败
    # # { "pk": 2, "username": "admin", "label": "admin(2)" }
    # # attrs 变量，表示展示的字段，有 pk,username 字段， 且 pk 字段是必须的， 比如 'attrs': ['pk']
    # # format 变量，表示label字段展示内容，里面的字段一定是属于 attrs 定义的字段，写错的话，可能会报错
    # # queryset 变量， 表示数据查询对象集合，注意：search-columns 方法中，该字段会有个 choices 变量，默认最多返回
    # #      SEARCH_CHOICES_MAX_COUNT（系统配置，默认 200）条，并带出 choices_truncated 标记，
    # #      超出部分的选项不会出现在下拉里，如果数据量特别大的时候，一定要自定义 input_type（如 api-search-user）， 否则会有问题
    # # input_type 变量， 自定义，如果存在，前端解析定义的类型 api-search-user ，并且 search-columns 方法中，choices变量为 []
    # #      如果数据量特别大的时候，推荐这种写法
    # # 目前，可以注释了，在父类里面，已经定义了 serializer_related_field 字段， 建议写到 extra_kwargs 里面，使用系统会自动生成
    # # 或者 按照下面方法自己定义。
    # # 为啥推荐写到 extra_kwargs ？ 写到extra_kwargs里面，系统会自动传一些参数， 可以省略 queryset , label 等参数
    # admin = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], label="管理员", required=True,
    #                                    format="{username}({pk})", queryset=UserInfo.objects,
    #                                    input_type='api-search-user')

    # # 目前，可以注释了，在父类里面，已经定义了 serializer_choice_field 字段， 系统会自动生成
    # category = LabeledChoiceField(choices=models.Book.CategoryChoices.choices,
    #                               default=models.Book.CategoryChoices.FICTION)

    # 自定义 input_type 教学字段：input_type 仅是前端组件渲染识别用（可自定义，但前端组件需对
    # 该 input_type 实现渲染）——`boolean` 由 RePlusPage 自动渲染为开关（绑定 row[field] 并调
    # partialUpdate 回写）。这里用 source=is_active 打通完整读写路径：列表开关点击即切换启用状态。
    # 前端自定义组件库 src/components/RePlusPage/src/components
    # 渲染组件定义 src/components/RePlusPage/src/utils/columns.tsx
    block = input_wrapper(serializers.BooleanField)(
        source="is_active",
        required=False,
        input_type="boolean",
        label="是否启用（自定义 input_type 演示）",
    )
