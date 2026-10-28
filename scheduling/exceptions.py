from django.utils.translation import gettext_lazy as _


class SchedulingError(Exception):
    """Base class for errors that can be shown to the user."""

    message = _('Something went wrong')

    def __init__(self, message=None):
        super().__init__(message or self.message)


class SlotGenerationError(SchedulingError):
    message = _('Slots cannot be generated for this day')


class InvalidDayRangeError(SlotGenerationError):
    message = _('Exam day must end after it starts')


class DayShorterThanSlotError(SlotGenerationError):
    message = _('Exam day is shorter than a single slot')


class SlotsAlreadyExistError(SlotGenerationError):
    message = _('Slots already exist for this day')


class OfferError(SchedulingError):
    message = _('Offer cannot be created')


class NotSlotOwnerError(OfferError):
    message = _('You do not own the slot you are offering')


class SameSlotError(OfferError):
    message = _('You cannot swap a slot with itself')


class WantedSlotFreeError(OfferError):
    message = _('The wanted slot is free, so you can just take it')


class DifferentExamError(OfferError):
    message = _('Slots belong to different exams')


class DeadlinePassedError(OfferError):
    message = _('The deadline for changing slots has passed')


class DuplicateOfferError(OfferError):
    message = _('You already have an active offer for this pair of slots')


class AcceptOfferError(SchedulingError):
    message = _('Offer cannot be accepted')


class OfferNotAwaitingError(AcceptOfferError):
    message = _('This offer is no longer active')


class OfferExpiredError(AcceptOfferError):
    message = _('This offer has expired')


class OfferOutdatedError(AcceptOfferError):
    message = _('The offering student no longer holds that slot')


class NotWantedSlotOwnerError(AcceptOfferError):
    message = _('You do not hold the slot this offer is for')
