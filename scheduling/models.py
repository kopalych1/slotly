from datetime import timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _


def day_from_now():
    return now() + timedelta(days=1)


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
