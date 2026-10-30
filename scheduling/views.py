from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from scheduling.exceptions import SchedulingError
from scheduling.models import Exam, ExamDay, Slot
from scheduling.services import release_slot, take_slot


@login_required
def index(request):

    return render(request, 'home.html')


@login_required
def exam_detail(request, pk: int):

    exams = Exam.objects.filter(
        Q(enrollment__user=request.user) | Q(owner=request.user)
    ).distinct()
    exam = get_object_or_404(exams, pk=pk)

    slots = Slot.objects.select_related('owner').order_by('start')
    examdays = ExamDay.objects.filter(exam=exam).prefetch_related(
        Prefetch('slot_set', queryset=slots)
    )

    # Prefetch
    examdays = exam.examday_set.all()  # pyright: ignore[reportAttributeAccessIssue]

    return render(
        request,
        'exam_detail.html',
        {
            'exam': exam,
            'examdays': examdays,
            'deadline_passed': now() >= exam.slots_change_deadline,
            'my_slot': Slot.objects.filter(exam=exam, owner=request.user).first(),
        },
    )


@login_required
@require_POST
def take_slot_view(request, slot_id: int):

    slot = get_object_or_404(Slot, pk=slot_id)

    try:
        take_slot(slot_id, request.user)
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, _('Slot successfully taken'))

    return redirect('exam_detail', pk=slot.exam_id)


@login_required
@require_POST
def release_slot_view(request, slot_id: int):
    slot = get_object_or_404(Slot, pk=slot_id)

    try:
        release_slot(slot_id, request.user)
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, _('Slot successfully released'))
    return redirect('exam_detail', pk=slot.exam_id)
