from django.contrib import admin

# Register your models here.
from notifications.models import *

admin.site.register(MessageContent)
admin.site.register(MessageUserRead)
admin.site.register(UserMsgSubscription)
admin.site.register(SystemMsgSubscription)
