from datetime import datetime

from django import forms
from django.forms import formset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from scheduling.models import Exam


class ExamForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = ('name', 'slot_duration', 'slots_change_deadline')

        labels = {  # noqa: RUF012
            'name': _('Name'),
            'slot_duration': _('Slot duration'),
            'slots_change_deadline': _('Swap deadline'),
        }

        widgets = {  # noqa: RUF012
            'slots_change_deadline': forms.DateTimeInput(
                attrs={'data-picker': 'datetime', 'autocomplete': 'off'},
                format='%Y-%m-%dT%H:%M',
            ),
        }


class DayForm(forms.Form):
    date = forms.DateField(
        label=_('Date'),
        widget=forms.DateInput(
            attrs={'data-picker': 'date', 'autocomplete': 'off'}, format='%Y-%m-%d'
        ),
    )
    start = forms.TimeField(
        label=_('From'),
        widget=forms.TimeInput(
            attrs={
                'placeholder': 'HH:MM',
                'inputmode': 'numeric',
                'autocomplete': 'off',
                'maxlength': '5',
            },
            format='%H:%M',
        ),
    )
    end = forms.TimeField(
        label=_('To'),
        widget=forms.TimeInput(
            attrs={
                'placeholder': 'HH:MM',
                'inputmode': 'numeric',
                'autocomplete': 'off',
                'maxlength': '5',
            },
            format='%H:%M',
        ),
    )

    def clean(self):
        cleaned = super().clean()
        day = cleaned.get('date')
        start = cleaned.get('start')
        end = cleaned.get('end')
        if day is None or start is None or end is None:
            return cleaned  # field errors are already reported

        if end <= start:
            raise forms.ValidationError(_('The day must end after it starts'))

        cleaned['start_dt'] = timezone.make_aware(datetime.combine(day, start))
        cleaned['end_dt'] = timezone.make_aware(datetime.combine(day, end))
        return cleaned


DayFormSet = formset_factory(DayForm, extra=0, can_delete=True)


class PasteInvitesForm(forms.Form):
    text = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'placeholder': 'Emma\tLaurent\temma.laurent@example.com\nLucas\tMartin\tlucas.martin@example.com\nanna@example.com'
            }
        ),
        max_length=10000,
    )
