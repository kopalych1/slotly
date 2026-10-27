from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


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
