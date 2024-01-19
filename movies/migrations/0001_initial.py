# Generated by Django 4.2.9 on 2024-01-19 08:39

import common.base.daobase
from django.db import migrations, models
import django.db.models.deletion
import system.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ActorInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('name', models.CharField(max_length=128, verbose_name='演员名字')),
                ('foreign_name', models.CharField(max_length=64, verbose_name='外文')),
                ('avatar', models.FileField(blank=True, max_length=256, null=True, upload_to=system.models.upload_directory_path, verbose_name='头像')),
                ('sex', models.SmallIntegerField(default=0, help_text='0：男 1：女 2：保密', verbose_name='性别')),
                ('birthday', models.DateField(verbose_name='出生日期')),
                ('introduction', models.TextField(blank=True, null=True, verbose_name='简介')),
                ('enable', models.BooleanField(default=True, verbose_name='是否启用')),
                ('douban', models.BigIntegerField(blank=True, default=0, null=True, verbose_name='豆瓣ID')),
                ('birthplace', models.CharField(blank=True, max_length=128, null=True, verbose_name='出生地')),
                ('profession', models.CharField(blank=True, max_length=128, null=True, verbose_name='职位')),
            ],
            options={
                'verbose_name': '演员',
                'verbose_name_plural': '演员',
            },
        ),
        migrations.CreateModel(
            name='ActorShip',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('who', models.CharField(blank=True, max_length=256, null=True, verbose_name='饰演')),
                ('actor_type', models.SmallIntegerField(choices=[(1, '导演'), (2, '编剧'), (3, '演员')], default=3, verbose_name='类型')),
            ],
            options={
                'verbose_name': '演员饰演中间表',
                'verbose_name_plural': '演员饰演中间表',
            },
        ),
        migrations.CreateModel(
            name='AliyunDrive',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('user_name', models.CharField(max_length=128, verbose_name='用户名')),
                ('nick_name', models.CharField(max_length=128, verbose_name='昵称')),
                ('user_id', models.CharField(max_length=32, verbose_name='用户ID')),
                ('default_drive_id', models.CharField(max_length=16, verbose_name='存储ID')),
                ('default_sbox_drive_id', models.CharField(max_length=16, verbose_name='保险箱ID')),
                ('access_token', common.base.daobase.AESCharField(max_length=1536, verbose_name='访问token')),
                ('refresh_token', common.base.daobase.AESCharField(max_length=512, verbose_name='刷新token')),
                ('avatar', models.CharField(max_length=512, verbose_name='头像地址')),
                ('expire_time', models.DateTimeField(auto_now_add=True, verbose_name='过期信息')),
                ('x_device_id', models.CharField(max_length=128, verbose_name='设备ID')),
                ('used_size', models.BigIntegerField(default=0, verbose_name='已经使用空间')),
                ('total_size', models.BigIntegerField(default=0, verbose_name='总空间大小')),
                ('enable', models.BooleanField(default=True, verbose_name='是否启用')),
                ('private', models.BooleanField(default=True, verbose_name='是否私有，若设置为否，则该网盘可被其他用户进行上传下载')),
                ('active', models.BooleanField(default=True, verbose_name='密钥是否可用')),
            ],
            options={
                'verbose_name': '阿里云网盘认证信息',
                'verbose_name_plural': '阿里云网盘认证信息',
            },
        ),
        migrations.CreateModel(
            name='AliyunFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('name', models.CharField(max_length=256, verbose_name='文件名字')),
                ('file_id', models.CharField(max_length=64, verbose_name='文件id')),
                ('drive_id', models.CharField(max_length=64, verbose_name='drive_id')),
                ('size', models.BigIntegerField(verbose_name='文件大小')),
                ('content_type', models.CharField(max_length=64, verbose_name='文件类型')),
                ('category', models.CharField(max_length=64, verbose_name='类别')),
                ('content_hash', models.CharField(max_length=64, verbose_name='content_hash')),
                ('crc64_hash', models.CharField(max_length=64, verbose_name='crc64_hash')),
                ('downloads', models.BigIntegerField(default=0, verbose_name='下载次数')),
                ('duration', models.FloatField(default=0, verbose_name='视频长度')),
                ('is_upload', models.BooleanField(default=True, verbose_name='是否是上传的文件数据')),
            ],
            options={
                'verbose_name': '文件信息',
                'verbose_name_plural': '文件信息',
            },
        ),
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('name', models.CharField(max_length=64, verbose_name='类别')),
                ('category_type', models.SmallIntegerField(choices=[(1, '视频渠道'), (2, '地区国家'), (3, '视频类型'), (4, '视频语言')], default=1, verbose_name='类型')),
                ('enable', models.BooleanField(default=True, verbose_name='是否启用')),
                ('rank', models.IntegerField(default=999, verbose_name='展示顺序')),
            ],
            options={
                'verbose_name': '类型',
                'verbose_name_plural': '类型',
                'ordering': ['rank'],
            },
        ),
        migrations.CreateModel(
            name='EpisodeInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('rank', models.IntegerField(default=999, verbose_name='多级电影顺序')),
                ('name', models.CharField(blank=True, max_length=64, null=True, verbose_name='电影单集名称')),
                ('extra_info', models.JSONField(blank=True, null=True, verbose_name='额外信息')),
                ('enable', models.BooleanField(default=True, verbose_name='是否启用')),
                ('views', models.BigIntegerField(default=0, verbose_name='观看次数')),
            ],
            options={
                'verbose_name': '单集影片信息',
                'verbose_name_plural': '单集影片信息',
            },
        ),
        migrations.CreateModel(
            name='FilmInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('name', models.CharField(max_length=128, verbose_name='电影片名')),
                ('title', models.CharField(max_length=256, verbose_name='电影译名')),
                ('poster', models.FileField(blank=True, null=True, upload_to=system.models.upload_directory_path, verbose_name='海报')),
                ('times', models.IntegerField(default=90, verbose_name='片长，分钟')),
                ('rate', models.DecimalField(decimal_places=1, default=5, max_digits=5, verbose_name='评分')),
                ('enable', models.BooleanField(default=True, verbose_name='是否启用')),
                ('views', models.BigIntegerField(default=0, verbose_name='观看次数')),
                ('release_date', models.DateField(verbose_name='上映时间')),
                ('introduction', models.TextField(blank=True, null=True, verbose_name='剧情')),
                ('douban', models.BigIntegerField(blank=True, default=0, null=True, verbose_name='豆瓣ID')),
                ('running', models.BooleanField(default=False, verbose_name='任务运行状态')),
            ],
            options={
                'verbose_name': '影片信息',
                'verbose_name_plural': '影片信息',
            },
        ),
        migrations.CreateModel(
            name='SwipeInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('name', models.CharField(blank=True, max_length=64, null=True, verbose_name='轮播名称')),
                ('picture', models.FileField(blank=True, null=True, upload_to=system.models.upload_directory_path, verbose_name='轮播图片')),
                ('route', models.CharField(blank=True, max_length=128, null=True, verbose_name='前端路由地址')),
                ('rank', models.IntegerField(default=999, verbose_name='轮播图片顺序')),
                ('enable', models.BooleanField(default=True, verbose_name='是否启用')),
            ],
            options={
                'verbose_name': '轮播图片',
                'verbose_name_plural': '轮播图片',
            },
        ),
        migrations.CreateModel(
            name='WatchHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_time', models.DateTimeField(auto_now_add=True, verbose_name='添加时间')),
                ('updated_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('description', models.CharField(blank=True, max_length=128, null=True, verbose_name='描述信息')),
                ('times', models.FloatField(verbose_name='播放的时间')),
                ('episode', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='movies.episodeinfo', verbose_name='单集视频')),
                ('film', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='movies.filminfo', verbose_name='视频')),
            ],
            options={
                'verbose_name': '播放历史',
                'verbose_name_plural': '播放历史',
            },
        ),
    ]