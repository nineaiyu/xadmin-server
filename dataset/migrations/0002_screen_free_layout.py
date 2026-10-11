# -*- coding: utf-8 -*-
"""大屏自由画布模式：layout_mode（grid/free）+ free_layout（设计画幅 + 像素窗格）。

存量数据零影响：layout_mode 缺省 grid、free_layout 缺省空，行为与迁移前一致
（投屏判定链：自由载荷非空 → 栅格载荷非空 → 轮播）。
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dataset", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="screen",
            name="layout_mode",
            field=models.CharField(
                choices=[("grid", "Grid"), ("free", "Free canvas")],
                default="grid",
                help_text="Canvas mode: grid uses layout, free uses free_layout",
                max_length=8,
                verbose_name="Layout mode",
            ),
        ),
        migrations.AddField(
            model_name="screen",
            name="free_layout",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Free pixel canvas: {design_w, design_h, panes} (empty = none)",
                verbose_name="Free layout",
            ),
        ),
    ]
