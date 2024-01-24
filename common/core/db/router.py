#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : router
# author : ly_13
# date : 12/18/2023

# https://docs.djangoproject.com/zh-hans/5.0/topics/db/multi-db/#automatic-database-routing
class DBRouter:
    """
    A router to control all database operations on models
    """

    def db_for_read(self, model, **hints):
        """
        建议用于读取“模型”类型对象的数据库。
        如果数据库操作可以提供有助于选择数据库的任何附加信息，它将在 hints 中提供。这里 below 提供了有效提示的详细信息。
        如果没有建议，则返回 None 。
        """
        # if model._meta.app_label == "auth":
        #     return "auth_db"
        return None

    def db_for_write(self, model, **hints):
        """
        建议用于写入模型类型对象的数据库。
        如果数据库操作可以提供有助于选择数据库的任何附加信息，它将在 hints 中提供。这里 below 提供了有效提示的详细信息。
        如果没有建议，则返回 None 。
        """
        # if model._meta.app_label in ["auth", "contenttypes"]:
        #     return "auth_db"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        """
        如果允许 obj1 和 obj2 之间的关系，返回 True 。如果阻止关系，返回 False ，或如果路由没意见，则返回 None。
        这纯粹是一种验证操作，由外键和多对多操作决定是否应该允许关系。
        如果没有路由有意见（比如所有路由返回 None），则只允许同一个数据库内的关系。
        """
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        决定是否允许迁移操作在别名为 db 的数据库上运行。如果操作运行，那么返回 True ，如果没有运行则返回 False ，或路由没有意见则返回 None 。
        app_label 参数是要迁移的应用程序的标签。
        model_name 由大部分迁移操作设置来要迁移的模型的 model._meta.model_name （模型 __name__ 的小写版本） 的值。
        对于 RunPython 和 RunSQL 操作的值是 None ，除非它们提示要提供它。
        """
        # if model._meta.app_label in ["auth", "contenttypes"]:
        #     return db == "auth_db"
        return None
