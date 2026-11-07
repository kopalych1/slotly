from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Min, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.forms import RegisterForm
from scheduling.exceptions import (
    InviteError,
    SchedulingError,
    TeacherCannotEnrollError,
)
from scheduling.forms import DayFormSet, ExamForm
from scheduling.models import Enrollment, Exam, ExamDay, Invite, Offer, Slot
from scheduling.services import (
    MAX_ACTIVE_OFFERS,
    accept_invite,
    accept_offer,
    create_offer,
    decline_offer,
    delete_exam,
    delete_exam_day,
    get_invite_problem,
    release_slot,
    save_exam,
    take_slot,
    withdraw_offer,
)


@login_required
def index(request):
    exams = Exam.objects.filter(
        Q(enrollment__user=request.user) | Q(owner=request.user)
    ).annotate(
        first_start=Min('examday__start'),
        last_end=Max('examday__end'),
    )
    current_time = now()

    upcoming = sorted(
        (e for e in exams if e.last_end is None or e.last_end >= current_time),  # pyright: ignore[reportAttributeAccessIssue]
        key=lambda e: (e.first_start is None, e.first_start),  # pyright: ignore[reportAttributeAccessIssue]
    )
    past = sorted(
        (e for e in exams if e.last_end is not None and e.last_end < current_time),  # pyright: ignore[reportAttributeAccessIssue]
        key=lambda e: e.last_end,  # pyright: ignore[reportAttributeAccessIssue]
        reverse=True,
    )

    all_exams = upcoming + past
    if not request.user.is_teacher and len(all_exams) == 1:
        return redirect('exam_detail', pk=all_exams[0].pk)

    return render(request, 'home.html', {'upcoming': upcoming, 'past': past})


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

    my_slot = Slot.objects.filter(exam=exam, owner=request.user).first()
    current_time = now()

    active_offers = list(
        Offer.objects.filter(
            offered_slot__exam=exam,
            status=Offer.Status.AWAITING,
            expires_at__gt=current_time,
        ).order_by('created_at')
    )

    sent = {}
    incoming = {}
    sent_count = 0

    for offer in active_offers:
        if offer.offeror_id == request.user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            sent[offer.wanted_slot_id] = offer  # pyright: ignore[reportAttributeAccessIssue]
            sent_count += 1
        if my_slot is not None and offer.wanted_slot_id == my_slot.pk:  # pyright: ignore[reportAttributeAccessIssue]
            incoming[offer.offered_slot_id] = offer.pk  # pyright: ignore[reportAttributeAccessIssue]

    history_list = list(
        Offer.objects.filter(offeror=request.user, offered_slot__exam=exam)
        .filter(~Q(status=Offer.Status.AWAITING) | Q(expires_at__lte=current_time))
        .select_related('wanted_slot')
        .order_by('-created_at')[:10]
    )

    return render(
        request,
        'exam_detail.html',
        {
            'exam': exam,
            'examdays': examdays,
            'deadline_passed': current_time >= exam.slots_change_deadline,
            'my_slot': my_slot,
            'sent_offers': sent,
            'incoming_offers': incoming,
            'offers_left': max(0, MAX_ACTIVE_OFFERS - sent_count),
            'history_list': history_list,
            'is_owner': exam.owner_id == request.user.pk,  # pyright: ignore[reportAttributeAccessIssue]
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


@login_required
@require_POST
def create_offer_view(request, slot_id: int):
    wanted_slot = get_object_or_404(Slot, pk=slot_id)

    offered_slot = Slot.objects.filter(
        owner_id=request.user.pk, exam_id=wanted_slot.exam_id
    ).first()

    if offered_slot is None:
        messages.error(request, _('You need a slot to offer a swap'))
        return redirect('exam_detail', pk=wanted_slot.exam_id)

    try:
        create_offer(request.user, offered_slot, wanted_slot)  # pyright: ignore[reportArgumentType]
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, _('Offer sent successfully'))

    return redirect('exam_detail', pk=wanted_slot.exam_id)


@login_required
@require_POST
def accept_offer_view(request, offer_id: int):
    offer = get_object_or_404(Offer.objects.select_related('wanted_slot'), pk=offer_id)

    try:
        swap_log = accept_offer(offer_id, request.user)
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        slot_time = timezone.localtime(swap_log.sender_slot_datetime)
        messages.success(
            request,
            _('Swapped: your new slot is %(time)s')
            % {'time': slot_time.strftime('%H:%M')},
        )

    return redirect('exam_detail', pk=offer.wanted_slot.exam_id)


@login_required
@require_POST
def decline_offer_view(request, offer_id: int):
    offer = get_object_or_404(Offer.objects.select_related('wanted_slot'), pk=offer_id)

    try:
        decline_offer(offer_id, request.user)
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, _('Offer declined'))

    return redirect('exam_detail', pk=offer.wanted_slot.exam_id)


@login_required
@require_POST
def offer_withdraw_view(request, offer_id: int):
    offer = get_object_or_404(Offer.objects.select_related('wanted_slot'), pk=offer_id)

    try:
        withdraw_offer(offer_id, request.user)
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, _('Offer withdrawn'))

    return redirect('exam_detail', pk=offer.wanted_slot.exam_id)


def join_invite(request, token: str):

    invite = get_object_or_404(Invite.objects.select_related('exam'), token=token)

    if request.user.is_authenticated:
        is_enrolled = Enrollment.objects.filter(
            user_id=request.user.pk,
            exam_id=invite.exam_id,  # pyright: ignore[reportAttributeAccessIssue]
        ).exists()
        if is_enrolled:
            return redirect('exam_detail', pk=invite.exam_id)  # pyright: ignore[reportAttributeAccessIssue]
        if request.user.is_teacher:
            return render(
                request,
                'join.html',
                {
                    'invite': invite,
                    'exam': invite.exam,
                    'problem': str(TeacherCannotEnrollError()),
                },
            )

    problem = get_invite_problem(invite)
    if problem is not None:
        return render(
            request,
            'join.html',
            {'invite': invite, 'exam': invite.exam, 'problem': str(problem)},
        )

    if request.method == 'POST':
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        try:
            accept_invite(token, request.user)
        except InviteError as e:
            messages.error(request, str(e))
            return redirect('join_invite', token=token)
        else:
            messages.success(request, _('Invite accepted'))
            return redirect('exam_detail', pk=invite.exam_id)  # pyright: ignore[reportAttributeAccessIssue]
    return render(
        request,
        'join.html',
        {
            'invite': invite,
            'exam': invite.exam,
            'problem': problem,
        },
    )


def register(request, token: str):

    invite = get_object_or_404(Invite.objects.select_related('exam'), token=token)

    if request.user.is_authenticated:
        return redirect('join_invite', token=token)

    if get_invite_problem(invite) is not None:
        return redirect('join_invite', token=token)

    if request.method == 'POST':
        form = RegisterForm(request.POST)

        if form.is_valid():
            user = form.save()
            login(request, user)

            try:
                accept_invite(token, user)
            except InviteError as e:
                messages.error(request, str(e))
                return redirect('join_invite', token=token)
            else:
                messages.success(request, _('Invite accepted'))
                return redirect('exam_detail', pk=invite.exam_id)  # pyright: ignore[reportAttributeAccessIssue]
    else:
        form = RegisterForm(initial={'email': invite.email})

    return render(
        request, 'register.html', {'form': form, 'invite': invite, 'exam': invite.exam}
    )


@login_required
def exam_edit(request, pk: int | None = None):
    if not request.user.is_teacher:
        raise PermissionDenied

    exam = (
        get_object_or_404(Exam, owner=request.user, pk=pk) if pk is not None else None
    )

    initial = (
        {
            'name': exam.name,
            'slot_duration': exam.slot_duration,
            'slots_change_deadline': exam.slots_change_deadline,
        }
        if exam
        else None
    )
    duration_locked = exam is not None and Slot.objects.filter(exam=exam).exists()

    if request.method == 'POST':
        exam_form = ExamForm(request.POST, initial=initial)
        day_formset = DayFormSet(request.POST)
    else:
        exam_form = ExamForm(initial=initial)
        day_formset = DayFormSet()

    if duration_locked:
        exam_form.fields['slot_duration'].disabled = True

    if request.method == 'POST':
        exam_valid = exam_form.is_valid()
        days_valid = day_formset.is_valid()

        if exam_valid and days_valid:
            days = [
                (f.cleaned_data['start_dt'], f.cleaned_data['end_dt'])
                for f in day_formset
                if f.cleaned_data and not f.cleaned_data.get('DELETE')
            ]
            try:
                saved = save_exam(
                    request.user,
                    exam,
                    name=exam_form.cleaned_data['name'],
                    slot_duration=exam_form.cleaned_data['slot_duration'],
                    deadline=exam_form.cleaned_data['slots_change_deadline'],
                    days=days,
                )
            except SchedulingError as e:
                exam_form.add_error(None, str(e))
            else:
                messages.success(request, _('Exam saved'))
                return redirect('exam_edit', pk=saved.pk)

    existing_days = list(
        ExamDay.objects.filter(exam=exam).prefetch_related(
            Prefetch(
                'slot_set',
                queryset=Slot.objects.select_related('owner').order_by('start'),
            )
        )
        if exam
        else []
    )
    for day in existing_days:
        day.has_owned_slots = any(slot.owner_id for slot in day.slot_set.all())  # pyright: ignore[reportAttributeAccessIssue]

    return render(
        request,
        'edit_exam.html',
        {
            'exam': exam,
            'form': exam_form,
            'day_formset': day_formset,
            'duration_locked': duration_locked,
            'existing_days': existing_days,
        },
    )


@login_required
@require_POST
def examday_delete(request, pk: int):

    examday = get_object_or_404(ExamDay, pk=pk, exam__owner=request.user)

    try:
        delete_exam_day(examday.pk, request.user)
    except SchedulingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, _('Day deleted'))
    return redirect('exam_edit', pk=examday.exam_id)  # pyright: ignore[reportAttributeAccessIssue]


def exam_stats(exam: Exam) -> dict[str, int]:
    slots = Slot.objects.filter(exam=exam).aggregate(
        total=Count('pk'), booked=Count('pk', filter=Q(owner__isnull=False))
    )
    return {
        'days': ExamDay.objects.filter(exam=exam).count(),
        'slots': slots['total'],
        'booked_slots': slots['booked'],
        'students': Enrollment.objects.filter(exam=exam).count(),
        'offers': Offer.objects.filter(
            offered_slot__exam=exam,
            status=Offer.Status.AWAITING,
            expires_at__gt=now(),
        ).count(),
    }


@login_required
def exam_delete(request, pk: int):
    exam = get_object_or_404(Exam, pk=pk, owner=request.user)
    stats = exam_stats(exam)
    needs_name = stats['booked_slots'] > 0

    if request.method == 'POST':
        if needs_name and request.POST.get('confirm_name', '').strip() != exam.name:
            messages.error(request, _('The name does not match. Nothing was deleted.'))
        else:
            try:
                delete_exam(exam.pk, request.user)
            except SchedulingError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, _('Exam deleted'))
                return redirect('home')

    return render(
        request,
        'exam_delete.html',
        {'exam': exam, 'stats': stats, 'needs_name': needs_name},
    )
