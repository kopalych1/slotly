from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    class Role(models.TextChoices):
        TEACHER = 'TCH', _('Teacher')
        STUDENT = 'STU', _('Student')

    role = models.CharField('Role', choices=Role, default=Role.STUDENT, max_length=20)
