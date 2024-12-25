from django.db import models
from django.utils import timezone
from pilkit.processors import ResizeToFill

from common.core.models import DbAuditModel, upload_directory_path, AutoCleanFileMixin
from common.fields.image import ProcessedImageField
from system.models import UserInfo


class Book(AutoCleanFileMixin, DbAuditModel):
    class CategoryChoices(models.IntegerChoices):
        DIRECTORY = 0, "小说"
        MENU = 1, "文学"
        PERMISSION = 2, "哲学"

    # choices 单选
    category = models.SmallIntegerField(choices=CategoryChoices, default=CategoryChoices.DIRECTORY,
                                        verbose_name="书籍类型")

    # ForeignKey  一对多关系
    admin = models.ForeignKey(to=UserInfo, verbose_name="管理员1", on_delete=models.CASCADE)
    admin2 = models.ForeignKey(to=UserInfo, verbose_name="管理员2", on_delete=models.CASCADE,
                               related_name="book_admin2")

    # ManyToManyField 多对多关系
    managers = models.ManyToManyField(to=UserInfo, verbose_name="操作人员1", blank=True, related_name="book_managers")
    managers2 = models.ManyToManyField(to=UserInfo, verbose_name="操作人员2", blank=True, related_name="book_managers2")
    # 图片上传，原图访问
    cover = models.ImageField(verbose_name="书籍封面原图", null=True, blank=True)

    # 图片上传，压缩访问， 比如库里面存的图片是 xxx/xxx/123.png ， 压缩访问路径可以为 xxx/xxx/123_1.jpg
    # 定义了 scales=[1, 2, 3, 4] ，因此有四个压缩链接文件名  123_1.jpg 123_2.jpg 123_3.jpg 123_4.jpg
    # 原图文件名 123.png
    avatar = ProcessedImageField(verbose_name="书籍封面缩略图", null=True, blank=True,
                                 upload_to=upload_directory_path,
                                 processors=[ResizeToFill(512, 512)],  # 默认存储像素大小
                                 scales=[1, 2, 3, 4],  # 缩略图可缩小倍数，
                                 format='png')

    # 文件上传
    book_file = models.FileField(verbose_name="书籍存储", upload_to=upload_directory_path, null=True, blank=True)

    # 普通字段
    name = models.CharField(verbose_name="书籍名称", max_length=100, help_text="书籍名称啊，随便填")
    isbn = models.CharField(verbose_name="标准书号", max_length=20)
    author = models.CharField(verbose_name="书籍作者", max_length=20, help_text="坐着大啊啊士大夫")
    publisher = models.CharField(verbose_name="出版社", max_length=20, default='大宇出版社')
    publication_date = models.DateTimeField(verbose_name="出版日期", default=timezone.now)
    price = models.FloatField(verbose_name="书籍售价", default=999.99)
    is_active = models.BooleanField(verbose_name="是否启用", default=False)

    class Meta:
        verbose_name = '书籍名称'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name}"
