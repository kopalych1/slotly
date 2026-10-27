from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (('Role', {'fields': ('role',)}),)  # pyright: ignore[reportAssignmentType, reportOperatorIssue]
    add_fieldsets = BaseUserAdmin.add_fieldsets + (('Role', {'fields': ('role',)}),)
    list_display = BaseUserAdmin.list_display + ('role',)  # pyright: ignore[reportOperatorIssue]
