import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _


def day_from_now():
    return now() + timedelta(days=1)


def generate_invite_token():
    return secrets.token_urlsafe(32)


class Exam(models.Model):
    name = models.CharField(_('Exam name'), max_length=200)
    owner = models.ForeignKey(
        'accounts.User', verbose_name=_('Owner'), on_delete=models.PROTECT
    )
    slot_duration = models.PositiveIntegerField(
        _('Slot time duration'),
        help_text=_('time in minutes'),
        default=20,
        validators=[MinValueValidator(1), MaxValueValidator(180)],
    )
    slots_change_deadline = models.DateTimeField(_('Slots change deadline'))

    class Meta:
        verbose_name = _('Exam')
        verbose_name_plural = _('Exams')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('Exam_detail', kwargs={'pk': self.pk})


class ExamDay(models.Model):
    exam = models.ForeignKey(
        'scheduling.Exam', verbose_name=_('Exam'), on_delete=models.CASCADE
    )
    start = models.DateTimeField(_('Exam day start datetime'))
    end = models.DateTimeField(_('Exam day end datetime'))

    class Meta:
        verbose_name = _('ExamDay')
        verbose_name_plural = _('ExamDays')

        ordering = ('start',)

    def clean(self) -> None:
        if not self.exam_id or not self.start or not self.end:  # pyright: ignore[reportAttributeAccessIssue]
            return
        if self.end <= self.start:
            raise ValidationError({'end': _('Exam day must end after it starts')})
        if self.end - self.start < timedelta(minutes=self.exam.slot_duration):
            raise ValidationError({'end': _('Exam day is shorter than a single slot')})
        super().clean()

    def __str__(self):
        return f'{self.exam.name} {self.start:%Y.%m.%d}'

    def get_absolute_url(self):
        return reverse('ExamDay_detail', kwargs={'pk': self.pk})


class Slot(models.Model):
    exam = models.ForeignKey(
        'scheduling.Exam', verbose_name=_('Exam'), on_delete=models.CASCADE
    )
    exam_day = models.ForeignKey(
        'scheduling.ExamDay', verbose_name=_('Exam day'), on_delete=models.CASCADE
    )
    start = models.DateTimeField(_('Slot start time'))
    owner = models.ForeignKey(
        'accounts.User',
        verbose_name=_('Owner'),
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = _('Slot')
        verbose_name_plural = _('Slots')
        ordering = ('start',)
        constraints = (
            models.UniqueConstraint(
                fields=['exam', 'start'],
                name='unique_slot_start_per_exam',
            ),
            models.UniqueConstraint(
                fields=['exam', 'owner'],
                name='unique_owner_per_exam',
                deferrable=models.Deferrable.DEFERRED,
            ),
        )

    def __str__(self):
        return f'{self.exam} {self.start:%H:%M}'

    def get_absolute_url(self):
        return reverse('Slot_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        self.exam_id = self.exam_day.exam_id
        super().save(*args, **kwargs)


class Offer(models.Model):
    class Status(models.TextChoices):
        AWAITING = 'W', _('Awaiting')
        ACCEPTED = 'A', _('Accepted')
        CANCELED = 'C', _('Canceled')
        EXPIRED = 'E', _('Expired')

    offeror = models.ForeignKey(
        'accounts.User', verbose_name=_('Offeror'), on_delete=models.CASCADE
    )
    offered_slot = models.ForeignKey(
        'scheduling.Slot',
        related_name='offers_as_offered',
        verbose_name=_('Offered slot'),
        on_delete=models.CASCADE,
    )
    wanted_slot = models.ForeignKey(
        'scheduling.Slot',
        related_name='offers_as_wanted',
        verbose_name=_('Wanted slot'),
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        _('Status'), choices=Status, default=Status.AWAITING, max_length=1
    )
    created_at = models.DateTimeField(_('Created at'), auto_now_add=True)
    expires_at = models.DateTimeField(_('Expires at'), default=day_from_now)

    class Meta:
        verbose_name = _('Offer')
        verbose_name_plural = _('Offers')

        constraints = (
            models.CheckConstraint(
                condition=~models.Q(offered_slot=models.F('wanted_slot')),
                name='offer_offered_slot_differs_from_wanted_slot',
            ),
            models.UniqueConstraint(
                fields=['offered_slot', 'wanted_slot'],
                condition=models.Q(status='W'),
                name='unique_awaiting_offer_per_slot_pair',
            ),
        )

    def __str__(self):
        return f'{self.offeror} {self.offered_slot} for {self.wanted_slot}'

    def get_absolute_url(self):
        return reverse('Offer_detail', kwargs={'pk': self.pk})


class SwapLog(models.Model):
    offer = models.ForeignKey(
        'scheduling.Offer',
        verbose_name=_('Offer'),
        null=True,
        on_delete=models.SET_NULL,
    )
    sender_name = models.CharField(_('Sender name'), max_length=100)
    receiver_name = models.CharField(_('Receiver name'), max_length=100)
    sender = models.ForeignKey(
        'accounts.User',
        verbose_name=_('Sender'),
        related_name='logs_as_sender',
        null=True,
        on_delete=models.SET_NULL,
    )
    receiver = models.ForeignKey(
        'accounts.User',
        verbose_name=_('Receiver'),
        related_name='logs_as_receiver',
        null=True,
        on_delete=models.SET_NULL,
    )
    sender_slot = models.ForeignKey(
        'scheduling.Slot',
        verbose_name=_('Sender slot'),
        related_name='slots_as_sender',
        null=True,
        on_delete=models.SET_NULL,
    )
    receiver_slot = models.ForeignKey(
        'scheduling.Slot',
        verbose_name=_('Receiver slot'),
        related_name='slots_as_receiver',
        null=True,
        on_delete=models.SET_NULL,
    )
    sender_slot_datetime = models.DateTimeField(_('Sender slot time'))
    receiver_slot_datetime = models.DateTimeField(_('Receiver slot time'))
    exam_name = models.CharField(_('Exam name'), max_length=200)
    exchange_datetime = models.DateTimeField(_('Exchange time'), auto_now_add=True)

    class Meta:
        verbose_name = _('SwapLog')
        verbose_name_plural = _('SwapLogs')

        ordering = ('-exchange_datetime',)

    def __str__(self):
        return f'{self.exam_name} {self.exchange_datetime:%Y-%m-%d %H:%M} {self.sender_name} {self.receiver_name}'


class Enrollment(models.Model):
    exam = models.ForeignKey(
        'scheduling.Exam', verbose_name=_('Exam'), on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        'accounts.User', verbose_name=_('User'), on_delete=models.CASCADE
    )
    created_at = models.DateTimeField(_('Created at'), auto_now_add=True)

    class Meta:
        verbose_name = _('Enrollment')
        verbose_name_plural = _('Enrollments')

        constraints = (
            models.UniqueConstraint(
                fields=['exam', 'user'],
                name='unique_enrollment_per_exam_user',
            ),
        )

    def __str__(self):
        return f'{self.exam.name} - {self.user}'


class Invite(models.Model):
    exam = models.ForeignKey(
        'scheduling.Exam', verbose_name=_('Exam'), on_delete=models.CASCADE
    )
    token = models.CharField(
        _('Token'),
        max_length=64,
        unique=True,
        default=generate_invite_token,
        editable=False,
    )
    name = models.CharField(
        _(
            'Student name',
        ),
        blank=True,
        default='',
        max_length=100,
    )
    email = models.EmailField(
        _(
            'Student email',
        ),
        blank=True,
        default='',
        max_length=254,
    )
    usage_counter = models.PositiveIntegerField(
        _('Usage counter'), default=0, editable=False
    )
    usage_limit = models.PositiveIntegerField(_('Usage limit'), null=True, blank=True)
    expires_at = models.DateTimeField(_('Expires at'), null=True, blank=True)
    is_revoked = models.BooleanField(_('Is revoked'), default=False)

    class Meta:
        verbose_name = _('Invite')
        verbose_name_plural = _('Invites')

        constraints = (
            models.CheckConstraint(
                condition=models.Q(usage_limit__isnull=True)
                | models.Q(usage_counter__lte=models.F('usage_limit')),
                name='invite_usage_within_limit',
            ),
            models.CheckConstraint(
                condition=models.Q(usage_limit__isnull=True)
                | models.Q(usage_limit__gt=0),
                name='invite_usage_limit_positive',
            ),
        )

    def __str__(self):
        return f'{self.exam} - {self.name or "shared"}'
