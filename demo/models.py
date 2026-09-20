from django.db import models
from django.utils import timezone
from pilkit.processors import ResizeToFill

from common.core.models import AutoCleanFileMixin, DbAuditModel, SoftDeleteModel, upload_directory_path
from common.fields.image import ProcessedImageField
from system.models import UploadFile, UserInfo


class Book(SoftDeleteModel, AutoCleanFileMixin, DbAuditModel):
    class CategoryChoices(models.IntegerChoices):
        FICTION = 0, "小说"
        LITERATURE = 1, "文学"
        PHILOSOPHY = 2, "哲学"

    class Status(models.TextChoices):
        """上架状态：由「提交上架」审批流驱动（终态经信号回写，见 demo/services.py）。"""

        DRAFT = "DRAFT", "草稿"
        PENDING = "PENDING", "审批中"
        ON_SHELF = "ON_SHELF", "已上架"
        REJECTED = "REJECTED", "已驳回"

    # choices 单选
    category = models.SmallIntegerField(
        choices=CategoryChoices, default=CategoryChoices.FICTION, verbose_name="书籍类型"
    )

    # ForeignKey  一对多关系
    admin = models.ForeignKey(to=UserInfo, verbose_name="管理员1", on_delete=models.CASCADE)
    admin2 = models.ForeignKey(
        to=UserInfo, verbose_name="管理员2", on_delete=models.CASCADE, related_name="book_admin2"
    )

    # ManyToManyField 多对多关系
    managers = models.ManyToManyField(to=UserInfo, verbose_name="操作人员1", blank=True, related_name="book_managers")
    managers2 = models.ManyToManyField(to=UserInfo, verbose_name="操作人员2", blank=True, related_name="book_managers2")
    # 图片上传，原图访问
    cover = models.ImageField(verbose_name="书籍封面原图", null=True, blank=True)

    # 图片上传，压缩访问， 比如库里面存的图片是 xxx/xxx/123.png ， 压缩访问路径可以为 xxx/xxx/123_1.jpg
    # 定义了 scales=[1, 2, 3, 4] ，因此有四个压缩链接文件名  123_1.jpg 123_2.jpg 123_3.jpg 123_4.jpg
    # 原图文件名 123.png
    avatar = ProcessedImageField(
        verbose_name="书籍封面缩略图",
        null=True,
        blank=True,
        upload_to=upload_directory_path,
        processors=[ResizeToFill(512, 512)],  # 默认存储像素大小
        scales=[1, 2, 3, 4],  # 缩略图可缩小倍数，
        format="png",
    )

    # 文件上传
    book_file = models.FileField(verbose_name="书籍存储", upload_to=upload_directory_path, null=True, blank=True)

    # 使用 UploadFile 关联（可选附件：模型层与接口层（serializer required=False）同口径）
    file = models.ForeignKey(
        to=UploadFile,
        related_name="book_file",
        verbose_name="书籍单个附件",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    files = models.ManyToManyField(to=UploadFile, related_name="book_files", verbose_name="书籍多附件", blank=True)

    # 普通字段
    name = models.CharField(verbose_name="书籍名称", max_length=100)
    isbn = models.CharField(verbose_name="标准书号", max_length=20)
    author = models.CharField(verbose_name="书籍作者", max_length=20)
    publisher = models.CharField(verbose_name="出版社", max_length=20, default="示例出版社")
    publication_date = models.DateTimeField(verbose_name="出版日期", default=timezone.now)
    price = models.FloatField(verbose_name="书籍售价", default=999.99)
    is_active = models.BooleanField(verbose_name="是否启用", default=False)

    # 上架状态：草稿 →（提交上架）→ 审批中 →（审批通过）→ 已上架 /（驳回）→ 已驳回
    status = models.CharField(
        verbose_name="上架状态",
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    # 上架审批流程实例：终态由 approval_instance_finished 信号回写 status（见 demo/services.py）
    instance = models.ForeignKey(
        "system.ApprovalInstance",
        related_name="demo_books",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="审批实例",
    )
    # 上架时间：审批通过（sync_book_instance）时写入，作为审批结果落下的业务痕迹
    on_shelf_time = models.DateTimeField(verbose_name="上架时间", null=True, blank=True)

    class Meta:
        verbose_name = "书籍名称"
        verbose_name_plural = verbose_name
        ordering = ("pk",)

    def __str__(self):
        return f"{self.name}"
