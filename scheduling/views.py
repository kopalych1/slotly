from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Prefetch, Q
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
from scheduling.models import Enrollment, Exam, ExamDay, Invite, Offer, Slot
from scheduling.services import (
    MAX_ACTIVE_OFFERS,
    accept_invite,
    accept_offer,
    create_offer,
    decline_offer,
    get_invite_problem,
    release_slot,
    take_slot,
)


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

    my_slot = Slot.objects.filter(exam=exam, owner=request.user).first()

    rows = list(
        Offer.objects.filter(
            offered_slot__exam=exam,
            status=Offer.Status.AWAITING,
            expires_at__gt=now(),
        ).values_list('pk', 'offeror_id', 'offered_slot_id', 'wanted_slot_id')
    )

    offered_slot_ids = set()  # Slots who I want to change with
    incoming = {}  # Slots who wants to change with me
    my_offers_count = 0

    for offer_pk, offeror_id, offered_id, wanted_id in rows:
        if offeror_id == request.user.pk:
            offered_slot_ids.add(wanted_id)
            my_offers_count += 1
        if my_slot is not None and wanted_id == my_slot.pk:
            incoming[offered_id] = offer_pk

    offers_left = max(0, MAX_ACTIVE_OFFERS - my_offers_count)

    return render(
        request,
        'exam_detail.html',
        {
            'exam': exam,
            'examdays': examdays,
            'deadline_passed': now() >= exam.slots_change_deadline,
            'my_slot': my_slot,
            'offered_slot_ids': offered_slot_ids,
            'incoming_offers': incoming,
            'offers_left': offers_left,
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
