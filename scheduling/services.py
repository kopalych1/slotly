from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from accounts.models import User
from scheduling.exceptions import (
    DayHasOwnedSlotsError,
    DayShorterThanSlotError,
    DeadlinePassedError,
    DifferentExamError,
    DuplicateInviteError,
    DuplicateOfferError,
    InvalidDayRangeError,
    InvalidUsageLimitError,
    InviteError,
    InviteExpiredError,
    InviteLimitReachedError,
    InviteNotFoundError,
    InviteRevokedError,
    NotEnrolledError,
    NotExamOwnerError,
    NotOfferorError,
    NotSlotOwnerError,
    NotWantedSlotOwnerError,
    OfferExpiredError,
    OfferLimitError,
    OfferNotAwaitingError,
    OfferOutdatedError,
    OverlappingDaysError,
    SameSlotError,
    SlotAlreadyOwnedError,
    SlotChangedError,
    SlotDurationLockedError,
    SlotNotFreeError,
    SlotsAlreadyExistError,
    TeacherCannotEnrollError,
    TeacherRequiredError,
    TooManyRowsError,
    WantedSlotFreeError,
)
from scheduling.models import Enrollment, Exam, ExamDay, Invite, Offer, Slot, SwapLog


def ensure_enrolled(user: User, exam_id: int) -> None:
    if not Enrollment.objects.filter(user=user, exam_id=exam_id).exists():
        raise NotEnrolledError()


def compute_slot_starts(
    start: datetime, end: datetime, slot_duration: timedelta
) -> list[datetime]:

    slots_amount = (end - start) // slot_duration

    return [start + i * slot_duration for i in range(slots_amount)]


def bulk_fill_slots(examday: ExamDay) -> list[Slot]:
    if examday.end <= examday.start:
        raise InvalidDayRangeError()

    slot_starts = compute_slot_starts(
        examday.start, examday.end, timedelta(minutes=examday.exam.slot_duration)
    )

    if len(slot_starts) == 0:
        raise DayShorterThanSlotError()

    slots = [
        Slot(exam=examday.exam, exam_day=examday, start=slot_start)
        for slot_start in slot_starts
    ]

    with transaction.atomic():
        if examday.slot_set.exists():  # pyright: ignore[reportAttributeAccessIssue]
            raise SlotsAlreadyExistError()
        return Slot.objects.bulk_create(slots)


OFFER_LIFETIME = timedelta(days=1)
MAX_ACTIVE_OFFERS = 3
MAX_IMPORT_ROWS = 50


def create_offer(offeror: User, offered_slot: Slot, wanted_slot: Slot) -> Offer:
    ensure_enrolled(offeror, exam_id=offered_slot.exam_id)
    if offered_slot.owner_id != offeror.pk:  # pyright: ignore[reportAttributeAccessIssue]
        raise NotSlotOwnerError()
    if offered_slot.pk == wanted_slot.pk:
        raise SameSlotError()
    if wanted_slot.owner_id is None:  # pyright: ignore[reportAttributeAccessIssue]
        raise WantedSlotFreeError()
    if offered_slot.exam_id != wanted_slot.exam_id:
        raise DifferentExamError()

    deadline = offered_slot.exam.slots_change_deadline
    current_time = now()
    if current_time >= deadline:
        raise DeadlinePassedError()

    current_offers = Offer.objects.filter(
        offered_slot__exam_id=offered_slot.exam_id,
        offeror=offeror,
        status=Offer.Status.AWAITING,
        expires_at__gt=current_time,
    ).count()

    if current_offers >= MAX_ACTIVE_OFFERS:
        raise OfferLimitError()

    try:
        with transaction.atomic():
            Offer.objects.filter(
                offered_slot=offered_slot,
                wanted_slot=wanted_slot,
                status=Offer.Status.AWAITING,
                expires_at__lte=current_time,
            ).update(status=Offer.Status.EXPIRED)

            return Offer.objects.create(
                offeror=offeror,
                offered_slot=offered_slot,
                wanted_slot=wanted_slot,
                expires_at=min(current_time + OFFER_LIFETIME, deadline),
            )
    except IntegrityError:
        raise DuplicateOfferError()


def accept_offer(offer_id: int, acceptor: User) -> SwapLog:
    with transaction.atomic():
        offer = Offer.objects.get(pk=offer_id)

        locked = {
            s.pk: s
            for s in Slot.objects.select_for_update()
            .filter(pk__in=(offer.wanted_slot_id, offer.offered_slot_id))  # pyright: ignore[reportAttributeAccessIssue]
            .order_by('pk')
        }

        offer = Offer.objects.select_for_update().get(pk=offer_id)
        wanted_slot = locked[offer.wanted_slot_id]  # pyright: ignore[reportAttributeAccessIssue]
        offered_slot = locked[offer.offered_slot_id]  # pyright: ignore[reportAttributeAccessIssue]

        ensure_enrolled(acceptor, exam_id=wanted_slot.exam_id)
        if offer.status != Offer.Status.AWAITING:
            raise OfferNotAwaitingError()
        if offer.expires_at <= now():
            raise OfferExpiredError()
        if wanted_slot.exam.slots_change_deadline <= now():
            raise DeadlinePassedError()
        if offered_slot.owner_id != offer.offeror_id:  # pyright: ignore[reportAttributeAccessIssue]
            raise OfferOutdatedError()
        if wanted_slot.owner_id != acceptor.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise NotWantedSlotOwnerError()

        offer.status = Offer.Status.ACCEPTED
        wanted_slot.owner = offer.offeror
        offered_slot.owner = acceptor  # pyright: ignore[reportAttributeAccessIssue]

        Offer.objects.filter(
            status=Offer.Status.AWAITING,
            offered_slot__in=(offered_slot, wanted_slot),
        ).exclude(pk=offer.pk).update(status=Offer.Status.CANCELED)

        offer.save(update_fields=['status'])
        wanted_slot.save(update_fields=['owner'])
        offered_slot.save(update_fields=['owner'])

        return SwapLog.objects.create(
            offer=offer,
            sender_name=offer.offeror.username,
            receiver_name=acceptor.username,
            sender=offer.offeror,
            receiver=acceptor,
            sender_slot=offered_slot,
            receiver_slot=wanted_slot,
            sender_slot_datetime=offered_slot.start,
            receiver_slot_datetime=wanted_slot.start,
            exam_name=offered_slot.exam.name,
        )


def release_slot(slot_id: int, user: User) -> None:
    with transaction.atomic():
        slot = Slot.objects.select_for_update().get(pk=slot_id)

        ensure_enrolled(user, exam_id=slot.exam_id)

        if slot.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise NotSlotOwnerError()

        if now() >= slot.exam.slots_change_deadline:
            raise DeadlinePassedError()

        slot.owner = None
        slot.save(update_fields=['owner'])

        Offer.objects.filter(
            Q(offered_slot=slot) | Q(wanted_slot=slot), status=Offer.Status.AWAITING
        ).update(status=Offer.Status.CANCELED)


def take_slot(slot_id: int, user: User) -> Slot:

    exam_id = Slot.objects.values_list('exam_id', flat=True).get(pk=slot_id)
    current_id = (
        Slot.objects.filter(exam_id=exam_id, owner=user)
        .values_list('pk', flat=True)
        .first()
    )

    with transaction.atomic():
        locked = {
            s.pk: s
            for s in Slot.objects.select_for_update()
            .filter(pk__in=(current_id, slot_id))
            .order_by('pk')
        }

        slot = locked[slot_id]
        user_slot = locked.get(current_id)

        ensure_enrolled(user, exam_id=slot.exam_id)

        if user_slot is not None and user_slot.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise SlotChangedError()

        if slot.owner_id == user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise SlotAlreadyOwnedError()
        if slot.owner_id:  # pyright: ignore[reportAttributeAccessIssue]
            raise SlotNotFreeError()
        if now() >= slot.exam.slots_change_deadline:
            raise DeadlinePassedError()

        slot.owner = user  # pyright: ignore[reportAttributeAccessIssue]
        slot.save(update_fields=['owner'])

        if user_slot:
            user_slot.owner = None
            user_slot.save(update_fields=['owner'])

            Offer.objects.filter(
                Q(offered_slot=user_slot) | Q(wanted_slot=user_slot),
                status=Offer.Status.AWAITING,
            ).update(status=Offer.Status.CANCELED)

        return slot


def decline_offer(offer_id: int, user: User):
    with transaction.atomic():
        offer = (
            Offer.objects.select_related('wanted_slot')
            .select_for_update(of=('self',))
            .get(pk=offer_id)
        )

        ensure_enrolled(user, exam_id=offer.wanted_slot.exam_id)

        if offer.status != Offer.Status.AWAITING:
            raise OfferNotAwaitingError()
        if offer.wanted_slot.owner_id != user.pk:
            raise NotWantedSlotOwnerError()

        offer.status = Offer.Status.DECLINED
        offer.save(update_fields=['status'])


def get_invite_problem(invite: Invite) -> InviteError | None:

    if invite.is_revoked:
        return InviteRevokedError()
    if invite.expires_at is not None and invite.expires_at <= now():
        return InviteExpiredError()
    if invite.usage_limit is not None and invite.usage_counter >= invite.usage_limit:
        return InviteLimitReachedError()
    return None


def accept_invite(token: str, user: User) -> Enrollment:
    with transaction.atomic():
        try:
            invite = (
                Invite.objects.select_for_update(of=('self',))
                .select_related('exam')
                .get(token=token)
            )
        except Invite.DoesNotExist:
            raise InviteNotFoundError()

        if user.is_teacher:
            raise TeacherCannotEnrollError()

        existing = Enrollment.objects.filter(exam=invite.exam, user=user).first()
        if existing is not None:
            return existing

        problem = get_invite_problem(invite)
        if problem is not None:
            raise problem

        enrollment = Enrollment.objects.create(exam=invite.exam, user=user)
        invite.usage_counter += 1
        invite.save(update_fields=['usage_counter'])

        return enrollment


def withdraw_offer(offer_id: int, user: User):
    with transaction.atomic():
        offer = (
            Offer.objects.select_related('wanted_slot')
            .select_for_update(of=('self',))
            .get(pk=offer_id)
        )

        ensure_enrolled(user, exam_id=offer.wanted_slot.exam_id)

        if offer.status != Offer.Status.AWAITING:
            raise OfferNotAwaitingError()
        if offer.offeror_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise NotOfferorError()

        offer.status = Offer.Status.WITHDRAWN
        offer.save(update_fields=['status'])


def save_exam(
    user: User,
    exam: Exam | None,
    name: str,
    slot_duration: int,  # in minutes
    deadline: datetime,
    days: list[tuple[datetime, datetime]],  # new days, timezone-aware
) -> Exam:
    if not user.is_teacher:
        raise TeacherRequiredError()

    slot_length = timedelta(minutes=slot_duration)

    with transaction.atomic():
        existing_days: list[tuple[datetime, datetime]] = []

        if exam is not None:
            exam = Exam.objects.select_for_update().get(pk=exam.pk)

            if exam.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
                raise NotExamOwnerError()
            if (
                exam.slot_duration != slot_duration
                and Slot.objects.filter(exam=exam).exists()
            ):
                raise SlotDurationLockedError()

            existing_days = list(
                ExamDay.objects.filter(exam=exam).values_list('start', 'end')
            )

        for start, end in days:
            if end <= start:
                raise InvalidDayRangeError()
            if not compute_slot_starts(start, end, slot_length):
                raise DayShorterThanSlotError()

        ranges = sorted(existing_days + days)
        for (_, prev_end), (next_start, _) in pairwise(ranges):
            if next_start < prev_end:
                raise OverlappingDaysError()

        if exam is None:
            exam = Exam.objects.create(
                name=name,
                owner=user,
                slot_duration=slot_duration,
                slots_change_deadline=deadline,
            )
        else:
            exam.name = name
            exam.slot_duration = slot_duration
            exam.slots_change_deadline = deadline
            exam.save(update_fields=['name', 'slot_duration', 'slots_change_deadline'])

        for start, end in days:
            day = ExamDay.objects.create(exam=exam, start=start, end=end)
            bulk_fill_slots(day)

        return exam


def delete_exam_day(examday_id: int, user: User) -> None:
    with transaction.atomic():
        examday = (
            ExamDay.objects.select_for_update(of=('self',))
            .select_related('exam')
            .get(pk=examday_id)
        )

        if examday.exam.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise NotExamOwnerError()

        slots = list(
            Slot.objects.select_for_update().filter(exam_day=examday).order_by('pk')
        )
        if any(slot.owner_id is not None for slot in slots):  # pyright: ignore[reportAttributeAccessIssue]
            raise DayHasOwnedSlotsError()

        examday.delete()


def delete_exam(exam_id: int, user) -> None:
    with transaction.atomic():
        exam = Exam.objects.select_for_update(of=('self',)).get(pk=exam_id)

        if exam.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
            raise NotExamOwnerError()

        exam.delete()


def create_invite(
    exam_id: int,
    user: User,
    usage_limit: int | None = None,
    expires_at: datetime | None = None,
) -> Invite:

    if not user.is_teacher:
        raise TeacherRequiredError()

    if usage_limit is not None and usage_limit < 1:
        raise InvalidUsageLimitError()

    exam = Exam.objects.get(pk=exam_id)

    if exam.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
        raise NotExamOwnerError()

    if now() >= exam.slots_change_deadline:
        raise DeadlinePassedError()

    return Invite.objects.create(
        exam_id=exam_id,
        usage_limit=usage_limit,
        expires_at=expires_at if expires_at else exam.slots_change_deadline,
    )


class SkipReason(models.TextChoices):
    INVALID_EMAIL = 'invalid_email', _('Invalid email')
    DUPLICATE = 'duplicate', _('Duplicate in the list')
    ALREADY_INVITED = 'already_invited', _('Already invited')
    ALREADY_ENROLLED = 'already_enrolled', _('Already joined this exam')


@dataclass(frozen=True)
class SkippedRow:
    row: dict[str, str]
    reason: SkipReason


@dataclass(frozen=True)
class PersonalInvitesResult:
    created: list[Invite]
    skipped: list[SkippedRow]


def create_personal_invites(
    exam_id: int,
    user: User,
    student_data: list[dict[str, str]],
    expires_at: datetime | None = None,
    dry_run: bool = False,
) -> PersonalInvitesResult:
    if len(student_data) > MAX_IMPORT_ROWS:
        raise TooManyRowsError()

    if not user.is_teacher:
        raise TeacherRequiredError()

    exam = Exam.objects.get(pk=exam_id)

    if exam.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
        raise NotExamOwnerError()

    if now() >= exam.slots_change_deadline:
        raise DeadlinePassedError()

    skipped: list[SkippedRow] = []
    candidates: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()

    for data in student_data:
        email = (data.get('email') or '').strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            skipped.append(SkippedRow(data, SkipReason.INVALID_EMAIL))
            continue
        if email in seen:
            skipped.append(SkippedRow(data, SkipReason.DUPLICATE))
            continue
        seen.add(email)
        candidates.append((email, data))

    emails = [email for email, _ in candidates]

    already_invited = set(
        Invite.objects.filter(exam_id=exam_id, is_revoked=False)
        .annotate(email_lower=Lower('email'))
        .filter(email_lower__in=emails)
        .values_list('email_lower', flat=True)
    )
    already_enrolled = set(
        Enrollment.objects.filter(exam_id=exam_id)
        .annotate(email_lower=Lower('user__email'))
        .filter(email_lower__in=emails)
        .values_list('email_lower', flat=True)
    )

    invites: list[Invite] = []
    for email, data in candidates:
        if email in already_enrolled:
            skipped.append(SkippedRow(data, SkipReason.ALREADY_ENROLLED))
        elif email in already_invited:
            skipped.append(SkippedRow(data, SkipReason.ALREADY_INVITED))
        else:
            invites.append(
                Invite(
                    exam_id=exam_id,
                    usage_limit=1,
                    expires_at=expires_at or exam.slots_change_deadline,
                    first_name=(data.get('first_name') or '').strip()[:100],
                    last_name=(data.get('last_name') or '').strip()[:100],
                    email=email,
                )
            )
    if dry_run:
        return PersonalInvitesResult(created=invites, skipped=skipped)

    try:
        with transaction.atomic():
            created = Invite.objects.bulk_create(invites)
    except IntegrityError:
        raise DuplicateInviteError() from None

    return PersonalInvitesResult(created=created, skipped=skipped)


def revoke_invite(invite_id: int, user: User) -> None:

    if not user.is_teacher:
        raise TeacherRequiredError()

    invite = Invite.objects.select_related('exam').get(pk=invite_id)
    if invite.exam.owner_id != user.pk:
        raise NotExamOwnerError()
    if not invite.is_revoked:
        invite.is_revoked = True
        invite.save(update_fields=['is_revoked'])


def remove_student(exam_id: int, student_id: int, user: User) -> None:
    exam = Exam.objects.get(pk=exam_id)

    if not user.is_teacher:
        raise TeacherRequiredError()

    if exam.owner_id != user.pk:  # pyright: ignore[reportAttributeAccessIssue]
        raise NotExamOwnerError()

    with transaction.atomic():
        try:
            enrollment = Enrollment.objects.get(exam=exam, user_id=student_id)
        except Enrollment.DoesNotExist:
            raise NotEnrolledError()

        slot = (
            Slot.objects.select_for_update()
            .filter(exam_id=exam.pk, owner_id=student_id)
            .first()
        )
        if slot is not None:
            slot.owner = None
            slot.save(update_fields=['owner'])
            Offer.objects.filter(
                Q(wanted_slot=slot) | Q(offered_slot=slot), status=Offer.Status.AWAITING
            ).update(status=Offer.Status.CANCELED)

        enrollment.delete()
