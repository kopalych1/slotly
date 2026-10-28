from datetime import timedelta

from django.db import transaction

from scheduling.exceptions import SlotGenerationError
from scheduling.models import ExamDay, Slot


def bulk_fill_slots(examday: ExamDay) -> list[Slot]:
    if examday.end <= examday.start:
        raise SlotGenerationError('Exam day must end after it starts')

    slot_duration = timedelta(minutes=examday.exam.slot_duration)
    slots_amount = (examday.end - examday.start) // slot_duration

    if slots_amount == 0:
        raise SlotGenerationError('Exam day is shorter than a single slot')

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
            raise SlotGenerationError('Slots already exist for this day')
        return Slot.objects.bulk_create(slots)
