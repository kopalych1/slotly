from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView, PasswordResetView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from accounts.forms import ProfileForm
from accounts.models import User


class PasswordReset(PasswordResetView):
    template_name = 'registration/password_reset_form.html'
    email_template_name = 'registration/password_reset_email.txt'
    subject_template_name = 'registration/password_reset_subject.txt'
    extra_email_context = {'site_url': settings.SITE_URL}  # noqa: RUF012


class PasswordChange(SuccessMessageMixin, PasswordChangeView):
    template_name = 'registration/password_change_form.html'
    success_url = reverse_lazy('profile')
    success_message = _('Password changed')


@login_required
def profile(request):
    form = ProfileForm(
        request.POST or None,
        instance=User.objects.get(pk=request.user.pk),
    )

    if form.is_valid():
        form.save()
        messages.success(request, _('Profile saved'))
        return redirect('profile')

    return render(request, 'profile.html', {'form': form})
