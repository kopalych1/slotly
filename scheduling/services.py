from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.timezone import now

from accounts.models import User
from scheduling.exceptions import (
    DayShorterThanSlotError,
    DeadlinePassedError,
    DifferentExamError,
    DuplicateOfferError,
    InvalidDayRangeError,
    NotSlotOwnerError,
    NotWantedSlotOwnerError,
    OfferExpiredError,
    OfferLimitError,
    OfferNotAwaitingError,
    OfferOutdatedError,
    SameSlotError,
    SlotAlreadyOwnedError,
    SlotChangedError,
    SlotNotFreeError,
    SlotsAlreadyExistError,
    WantedSlotFreeError,
)
from scheduling.models import ExamDay, Offer, Slot, SwapLog


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
MAX_ACTIVE_OFFERS = 3


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

        if offer.status != Offer.Status.AWAITING:
            raise OfferNotAwaitingError()
        if offer.wanted_slot.owner_id != user.pk:
            raise NotWantedSlotOwnerError()

        offer.status = Offer.Status.CANCELED
        offer.save(update_fields=['status'])
