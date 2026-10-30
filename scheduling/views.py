from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, render

from scheduling.models import Exam, ExamDay, Slot


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

    return render(request, 'exam_detail.html', {'exam': exam, 'examdays': examdays})
