#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializer
# author : ly_13
# date : 6/12/2024
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from common.fields.utils import input_wrapper
from demo import models


class BookSerializer(BaseModelSerializer):
    class Meta:
        model = models.Book
        ## pk 字段用于前端删除，更新等标识，如果有删除更新等，必须得加上 pk 字段
        ## 数据返回的字段，该字段受字段权限控制
        fields = [
            'pk', 'name', 'isbn', 'category', 'is_active', 'author', 'publisher', 'publication_date', 'price', 'block',
            'created_time', 'admin', 'admin2', 'managers', 'managers2', 'avatar', 'cover', 'book_file', 'updated_time',
        ]
        ## 仅用于前端table表格字段有顺序的展示，如果没定义，默认使用 fields 定义的变量
        ## 为啥要有这个变量？ 一般情况下，前端table表格宽度不够，不需要显示太多字段，就可以通过这个变量来控制显示的字段
        table_fields = [
            'pk', 'cover', 'category', 'name', 'is_active', 'isbn', 'author', 'publisher', 'publication_date', 'price',
            'book_file'
        ]

        # fields_unexport = ['pk']  # 导入导出文件时，忽略该字段

        # read_only_fields = ['pk']  # 表示pk字段只读, 和 extra_kwargs 定义的 pk 含义一样

        ## 构建字段的额外参数
        # # extra_kwargs包含了admin 单对多的两种方式，managers 多对多的两种方式，区别在于自定义的input_type，
        # # 观察前端页面变化和 search-columns 请求的数据
        extra_kwargs = {
            'pk': {'read_only': True},  # 表示pk字段只读
            'admin': {
                'attrs': ['pk', 'username'], 'required': True, 'format': "{username}({pk})",
                'input_type': 'api-search-user'
            },
            'admin2': {
                'attrs': ['pk', 'username'], 'required': True, 'format': "{username}({pk})",
            },
            'managers': {
                'attrs': ['pk', 'username'], 'required': True, 'format': "{username}({pk})",
                'input_type': 'api-search-user'
            },
            'managers2': {
                'attrs': ['pk', 'username'], 'required': False, 'format': "{username}({pk})",
            }
        }

    # # 该方法定义了管理字段，和 extra_kwargs 定义的 admin 含义一样，该字段会被序列化为
    # # 定义带有关联关系的字段，比如上面的admin，则额外参数中，input_type 和 attrs 至少存在一个，要不然前端可能会解析失败
    # # { "pk": 2, "username": "admin", "label": "admin(2)" }
    # # attrs 变量，表示展示的字段，有 pk,username 字段， 且 pk 字段是必须的， 比如 'attrs': ['pk']
    # # format 变量，表示label字段展示内容，里面的字段一定是属于 attrs 定义的字段，写错的话，可能会报错
    # # queryset 变量， 表示数据查询对象集合，注意：search-columns 方法中，该字段会有个 choices 变量，并且包含所有queryset数据，
    # #      如果数据量特别大的时候，一定要自定义 input_type， 否则会有问题
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
    #                               default=models.Book.CategoryChoices.DIRECTORY)

    # 自定义 input_type ，设置了 read_only=True 意味着只能通过详情查看，在新增和编辑页面不展示该字段
    # input_type 仅是前端组件渲染识别用， 可以自定义input_type ,但是前端组件得对定义的input_type 进行渲染
    # 前端自定义组件库 src/components/RePlusPage/src/components
    # 渲染组件定义 src/components/RePlusPage/src/utils/columns.tsx
    block = input_wrapper(serializers.SerializerMethodField)(read_only=True, input_type='boolean',
                                                             label="自定义input_type")

    def get_block(self, obj):
        return obj.is_active
