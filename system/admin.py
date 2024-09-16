# Register your models here.
from django.contrib import admin

# Register your models here.
from system.models import *

admin.site.register(UserInfo)
admin.site.register(DeptInfo)
admin.site.register(ModelLabelField)
admin.site.register(UserLoginLog)
admin.site.register(OperationLog)
admin.site.register(MenuMeta)
admin.site.register(Menu)
admin.site.register(DataPermission)
admin.site.register(FieldPermission)
admin.site.register(UserRole)
admin.site.register(UploadFile)
admin.site.register(SystemConfig)
admin.site.register(UserPersonalConfig)
