from typing import Any

from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.forms.models import ModelForm
from django.utils.translation import gettext_lazy as _

from accounts.models import User


class RegisterForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True


class ProfileForm(ModelForm):
    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('email', 'first_name', 'last_name'):
            self.fields[name].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        others = User.objects.exclude(pk=self.instance.pk)
        if others.filter(email__iexact=email).exists():
            raise ValidationError(_('This email is already used by another account'))
        return email

    def clean_username(self):
        username = self.cleaned_data['username']
        others = User.objects.exclude(pk=self.instance.pk)
        if others.filter(username__iexact=username).exists():
            raise ValidationError(_('This username is already taken'))
        return username
