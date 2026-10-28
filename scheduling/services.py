from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils.timezone import now

from accounts.models import User
from scheduling.exceptions import (
    DayShorterThanSlotError,
    DeadlinePassedError,
    DifferentExamError,
    DuplicateOfferError,
    InvalidDayRangeError,
    NotSlotOwnerError,
    SameSlotError,
    SlotsAlreadyExistError,
    WantedSlotFreeError,
)
from scheduling.models import ExamDay, Offer, Slot


def bulk_fill_slots(examday: ExamDay) -> list[Slot]:
    if examday.end <= examday.start:
        raise InvalidDayRangeError()

    slot_duration = timedelta(minutes=examday.exam.slot_duration)
    slots_amount = (examday.end - examday.start) // slot_duration

    if slots_amount == 0:
        raise DayShorterThanSlotError()

    slots = [
        Slot(
            exam=examday.exam,
            exam_day=examday,
            start=examday.start + i * slot_duration,
        )
        for i in range(slots_amount)
    ]

    with transaction.atomic():
        if examday.slot_set.exists():  # pyright: ignore[reportAttributeAccessIssue]
            raise SlotsAlreadyExistError()
        return Slot.objects.bulk_create(slots)


OFFER_LIFETIME = timedelta(days=1)


def create_offer(offeror: User, offered_slot: Slot, wanted_slot: Slot) -> Offer:
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
