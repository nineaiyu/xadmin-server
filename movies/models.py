from django.conf import settings
from django.db import models

from common.base.daobase import AESCharField
from system.models import upload_directory_path, DbBaseModel


# Create your models here.

class AliyunDrive(DbBaseModel):
    owner = models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="所属用户")
    user_name = models.CharField(max_length=128, verbose_name="用户名")
    nick_name = models.CharField(max_length=128, verbose_name="昵称")
    user_id = models.CharField(max_length=32, verbose_name="用户ID")
    default_drive_id = models.CharField(max_length=16, verbose_name="存储ID")
    default_sbox_drive_id = models.CharField(max_length=16, verbose_name="保险箱ID")
    access_token = AESCharField(max_length=1536, verbose_name="访问token")
    refresh_token = AESCharField(max_length=512, verbose_name="刷新token")
    avatar = models.CharField(max_length=512, verbose_name="头像地址")
    expire_time = models.DateTimeField(verbose_name="过期信息", auto_now_add=True)
    x_device_id = models.CharField(verbose_name="设备ID", max_length=128)
    used_size = models.BigIntegerField(verbose_name="已经使用空间", default=0)
    total_size = models.BigIntegerField(verbose_name="总空间大小", default=0)
    enable = models.BooleanField(default=True, verbose_name="是否启用")
    private = models.BooleanField(default=True, verbose_name="是否私有，若设置为否，则该网盘可被其他用户进行上传下载")
    active = models.BooleanField(default=True, verbose_name="密钥是否可用")

    class Meta:
        verbose_name = '阿里云网盘认证信息'
        verbose_name_plural = "阿里云网盘认证信息"
        unique_together = ('owner', 'user_id')

    def __str__(self):
        return f"所属用户:{self.owner}-网盘用户名:{self.user_name}-网盘昵称:{self.nick_name}-是否启用:{self.enable}"


class AliyunFile(DbBaseModel):
    owner = models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="所属用户")
    aliyun_drive = models.ForeignKey(to=AliyunDrive, on_delete=models.CASCADE, verbose_name="所属阿里云盘")
    name = models.CharField(max_length=256, verbose_name="文件名字")
    file_id = models.CharField(max_length=64, verbose_name="文件id")
    drive_id = models.CharField(max_length=64, verbose_name="drive_id")
    size = models.BigIntegerField(verbose_name="文件大小")
    content_type = models.CharField(max_length=64, verbose_name="文件类型")
    category = models.CharField(max_length=64, verbose_name="类别")
    content_hash = models.CharField(max_length=64, verbose_name="content_hash")
    crc64_hash = models.CharField(max_length=64, verbose_name="crc64_hash")
    downloads = models.BigIntegerField(verbose_name="下载次数", default=0)
    duration = models.FloatField(verbose_name="视频长度", default=0)

    class Meta:
        verbose_name = '文件信息'
        verbose_name_plural = "文件信息"

    def __str__(self):
        return f"所属用户:{self.owner}-文件名:{self.name}-下载次数:{self.downloads}-文件大小:{self.size}"


class Category(DbBaseModel):
    name = models.CharField(max_length=64, verbose_name="类别")
    category_type_choices = ((1, '视频渠道'), (2, '地区国家'), (3, '视频类型'), (4, '视频语言'), (5, '视频字幕'))
    category_type = models.SmallIntegerField(verbose_name="类型", choices=category_type_choices, default=1)
    enable = models.BooleanField(default=True, verbose_name="是否启用")
    rank = models.IntegerField(verbose_name="展示顺序", default=999)

    class Meta:
        ordering = ['rank']
        verbose_name = '类型'
        verbose_name_plural = "类型"
        unique_together = ('category_type', 'name')

    @classmethod
    def get_channel_category(cls):
        return cls.objects.filter(category_type=1, enable=True).all()

    @classmethod
    def get_region_category(cls):
        return cls.objects.filter(category_type=2, enable=True).all()

    @classmethod
    def get_video_category(cls):
        return cls.objects.filter(category_type=3, enable=True).all()

    @classmethod
    def get_language_category(cls):
        return cls.objects.filter(category_type=4, enable=True).all()

    @classmethod
    def get_subtitle_category(cls):
        return cls.objects.filter(category_type=5, enable=True).all()


class ActorInfo(DbBaseModel):
    name = models.CharField(max_length=64, verbose_name="演员名字", unique=True)
    foreign_name = models.CharField(max_length=64, verbose_name="外文")
    avatar = models.FileField(verbose_name="头像", null=True, blank=True, upload_to=upload_directory_path)
    sex = models.SmallIntegerField(verbose_name="性别", default=0, help_text='0：男 1：女 2：保密')
    birthday = models.DateField(verbose_name="出生日期")
    introduction = models.TextField(verbose_name="简介", null=True, blank=True)
    enable = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        verbose_name = '演员'
        verbose_name_plural = "演员"

    def delete(self, using=None, keep_parents=False):
        if self.avatar:
            self.avatar.delete()  # 删除存储的文件
        return super().delete(using, keep_parents)


class FilmInfo(DbBaseModel):
    name = models.CharField(max_length=64, verbose_name="电影片名")
    title = models.CharField(max_length=64, verbose_name="电影译名")
    poster = models.FileField(verbose_name="海报", null=True, blank=True, upload_to=upload_directory_path)
    channel = models.ManyToManyField(to=Category, verbose_name="频道，电影，电视，综艺等分类",
                                     related_name='channel_category')
    category = models.ManyToManyField(to=Category, verbose_name="类别，科幻，灾难，武侠等分类",
                                      related_name='film_category')
    region = models.ManyToManyField(to=Category, verbose_name="地区，内地，中国台湾，中国香港，欧洲等分类",
                                    related_name='region_category')
    language = models.ManyToManyField(to=Category, verbose_name="语言，中文，英语，韩语等",
                                      related_name='language_category')
    subtitle = models.ManyToManyField(to=Category, verbose_name="字幕，中英对照",
                                      related_name='subtitle_category')

    director = models.ManyToManyField(to=ActorInfo, verbose_name="导演", related_name='director_actor')
    starring = models.ManyToManyField(to=ActorInfo, verbose_name="演员", related_name='starring_actor')
    times = models.IntegerField(verbose_name="片长，分钟", default=90)
    rate = models.FloatField(verbose_name="评分", default=5)
    enable = models.BooleanField(default=True, verbose_name="是否启用")
    views = models.BigIntegerField(verbose_name="观看次数", default=0)
    release_date = models.DateField(verbose_name="上映时间")
    introduction = models.TextField(verbose_name="剧情", null=True, blank=True)

    class Meta:
        verbose_name = '影片信息'
        verbose_name_plural = "影片信息"

    def delete(self, using=None, keep_parents=False):
        if self.poster:
            self.poster.delete()  # 删除存储的文件
        return super().delete(using, keep_parents)

    def __str__(self):
        return f"文件名:{self.name}-观看次数:{self.views}-片长:{self.times}"


class EpisodeInfo(DbBaseModel):
    rank = models.IntegerField(verbose_name="多级电影顺序", default=999)
    name = models.CharField(max_length=64, verbose_name="电影单集名称", null=True, blank=True)
    files = models.OneToOneField(to=AliyunFile, verbose_name="视频文件", on_delete=models.CASCADE)
    extra_info = models.JSONField(verbose_name="额外信息", null=True, blank=True)
    enable = models.BooleanField(default=True, verbose_name="是否启用")
    film = models.ForeignKey(to=FilmInfo, verbose_name="视频文件列表", on_delete=models.SET_NULL, null=True, blank=True)
    views = models.BigIntegerField(verbose_name="观看次数", default=0)

    class Meta:
        verbose_name = '单集影片信息'
        verbose_name_plural = "单集影片信息"

    def __str__(self):
        return f"单集文件名:{self.name}"


class WatchHistory(DbBaseModel):
    owner = models.ForeignKey(to=settings.AUTH_USER_MODEL, related_query_name='history_query', null=True, blank=True,
                              verbose_name='创建人', on_delete=models.CASCADE)
    film = models.ForeignKey(to=FilmInfo, verbose_name="视频", on_delete=models.CASCADE)
    episode = models.ForeignKey(to=EpisodeInfo, verbose_name="单集视频", on_delete=models.CASCADE)
    times = models.FloatField(verbose_name="播放的时间")

    class Meta:
        verbose_name = '播放历史'
        verbose_name_plural = "播放历史"
        unique_together = ('owner', 'film')

    def __str__(self):
        return f"播放历史:{self.episode} {self.owner}"


class SwipeInfo(DbBaseModel):
    name = models.CharField(max_length=64, verbose_name="轮播名称", null=True, blank=True)
    picture = models.FileField(verbose_name="轮播图片", null=True, blank=True, upload_to=upload_directory_path)
    route = models.CharField(max_length=128, verbose_name="前端路由地址", null=True, blank=True)
    rank = models.IntegerField(verbose_name="轮播图片顺序", default=999)
    enable = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        verbose_name = '轮播图片'
        verbose_name_plural = "轮播图片"

    def delete(self, using=None, keep_parents=False):
        if self.picture:
            self.picture.delete()  # 删除存储的文件
        return super().delete(using, keep_parents)

    def __str__(self):
        return f"轮播图片:{self.name} {self.picture}"
