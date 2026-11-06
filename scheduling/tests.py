from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.timezone import localdate, localtime, now

from accounts.models import User
from scheduling.exceptions import (
    DayHasOwnedSlotsError,
    DayShorterThanSlotError,
    DeadlinePassedError,
    DifferentExamError,
    DuplicateOfferError,
    InvalidDayRangeError,
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
    SlotDurationLockedError,
    SlotGenerationError,
    SlotNotFreeError,
    SlotsAlreadyExistError,
    TeacherCannotEnrollError,
    TeacherRequiredError,
    WantedSlotFreeError,
)
from scheduling.forms import ExamForm
from scheduling.models import Enrollment, Exam, ExamDay, Invite, Offer, Slot, SwapLog
from scheduling.services import (
    accept_invite,
    accept_offer,
    bulk_fill_slots,
    compute_slot_starts,
    create_offer,
    decline_offer,
    delete_exam_day,
    release_slot,
    save_exam,
    take_slot,
    withdraw_offer,
)


class SchedulingTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(
            username='teacher', password='pass', role=User.Role.TEACHER
        )
        cls.students = [
            User.objects.create_user(
                username=f'student{i}', password='pass', role=User.Role.STUDENT
            )
            for i in range(3)
        ]
        cls.exam = Exam.objects.create(
            name='Test exam',
            owner=cls.teacher,
            slot_duration=20,
            slots_change_deadline=now() + timedelta(days=7),
        )
        Enrollment.objects.bulk_create(
            Enrollment(exam=cls.exam, user=student) for student in cls.students
        )
        cls.examday = ExamDay.objects.create(
            exam=cls.exam,
            start=now() + timedelta(days=7),
            end=now() + timedelta(days=7, hours=3),
        )
        cls.slots = bulk_fill_slots(cls.examday)  # 9 slots

        for slot, student in zip(cls.slots, cls.students):
            slot.owner = student  # pyright: ignore[reportAttributeAccessIssue]
            slot.save()

    @staticmethod
    def owner_of(slot: Slot):
        return Slot.objects.get(pk=slot.pk).owner


class ComputeSlotStartsTests(SimpleTestCase):
    def setUp(self):
        self.start = now().replace(second=0, microsecond=0)
        self.step = timedelta(minutes=20)

    def test_exact_fit(self):
        starts = compute_slot_starts(
            self.start, self.start + timedelta(hours=1), self.step
        )

        self.assertEqual(
            starts,
            [self.start, self.start + self.step, self.start + 2 * self.step],
        )

    def test_partial_last_slot_is_dropped(self):
        starts = compute_slot_starts(
            self.start, self.start + timedelta(minutes=50), self.step
        )

        self.assertEqual(len(starts), 2)

    def test_shorter_than_one_slot_gives_empty_list(self):
        starts = compute_slot_starts(
            self.start, self.start + timedelta(minutes=10), self.step
        )

        self.assertEqual(starts, [])

    def test_end_not_after_start_gives_empty_list(self):
        self.assertEqual(compute_slot_starts(self.start, self.start, self.step), [])
        self.assertEqual(
            compute_slot_starts(self.start, self.start - self.step, self.step), []
        )


class BulkFillSlotsTests(TestCase):
    def make_day(self, hours: float, start=None) -> ExamDay:
        if start is None:
            start = now() + timedelta(days=1)
        return ExamDay.objects.create(
            exam=self.exam, start=start, end=start + timedelta(hours=hours)
        )

    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(
            username='teacher', password='pass', role=User.Role.TEACHER
        )
        cls.exam = Exam.objects.create(
            name='Test exam',
            owner=cls.teacher,
            slot_duration=20,
            slots_change_deadline=now() + timedelta(days=7),
        )

    def test_creates_slot_per_duration(self):
        start = now() + timedelta(days=1)
        day = self.make_day(start=start, hours=3)

        bulk_fill_slots(day)

        slots = Slot.objects.filter(exam_day=day).order_by('start')

        self.assertEqual(slots.count(), 9)
        self.assertEqual(slots.first().start, start)  # pyright: ignore[reportOptionalMemberAccess]
        self.assertEqual(slots.last().start, start + timedelta(minutes=160))  # pyright: ignore[reportOptionalMemberAccess]

    def test_raises_if_slots_already_exist(self):

        day = self.make_day(hours=3)
        bulk_fill_slots(day)

        with self.assertRaises(SlotsAlreadyExistError):
            bulk_fill_slots(day)

    def test_raises_if_day_shorter_than_slot(self):

        short_day = self.make_day(5 / 60)

        with self.assertRaises(DayShorterThanSlotError):
            bulk_fill_slots(short_day)

    def test_raises_if_end_before_start(self):

        day = ExamDay.objects.create(
            exam=self.exam,
            start=now() + timedelta(days=2),
            end=now() + timedelta(days=1),
        )

        with self.assertRaises(InvalidDayRangeError):
            bulk_fill_slots(day)


class CreateOfferTests(SchedulingTestCase):
    def test_creates_offer(self):
        offer = create_offer(
            self.students[0],
            self.slots[0],
            self.slots[1],
        )

        self.assertEqual(Offer.objects.count(), 1)
        self.assertEqual(offer.offeror, self.students[0])
        self.assertEqual(offer.offered_slot, self.slots[0])
        self.assertEqual(offer.wanted_slot, self.slots[1])
        self.assertEqual(offer.status, Offer.Status.AWAITING)
        self.assertLessEqual(offer.expires_at, self.exam.slots_change_deadline)
        self.assertLessEqual(offer.expires_at, now() + timedelta(hours=25))
        self.assertGreaterEqual(offer.expires_at, now() + timedelta(hours=23))

    def test_raises_if_student_not_owner_of_slot(self):
        with self.assertRaises(NotSlotOwnerError):
            create_offer(self.students[0], self.slots[1], self.slots[0])

    def test_raises_if_wanted_slot_and_owned_slot_are_same(self):
        with self.assertRaises(SameSlotError):
            create_offer(self.students[0], self.slots[0], self.slots[0])

    def test_raises_if_wanted_slot_is_free(self):
        with self.assertRaises(WantedSlotFreeError):
            create_offer(self.students[0], self.slots[0], self.slots[8])

    def test_different_exam_slot(self):
        different_exam = Exam.objects.create(
            name='Fake Exam',
            owner=self.teacher,
            slots_change_deadline=now() + timedelta(days=7),
        )
        different_slot = bulk_fill_slots(
            ExamDay.objects.create(
                exam=different_exam,
                start=now() + timedelta(days=7),
                end=now() + timedelta(days=7, hours=1),
            )
        )[0]

        # Slot must be owned not to throw WantedSlotFreeError
        different_slot.owner = self.students[2]  # pyright: ignore[reportAttributeAccessIssue]
        different_slot.save()

        with self.assertRaises(DifferentExamError):
            create_offer(self.students[0], self.slots[0], different_slot)

    def test_passed_deadline_slot(self):

        self.exam.slots_change_deadline = now() - timedelta(days=1)
        self.exam.save()

        with self.assertRaises(DeadlinePassedError):
            create_offer(self.students[0], self.slots[0], self.slots[1])

    def test_same_offer_twice(self):
        create_offer(self.students[0], self.slots[0], self.slots[1])
        with self.assertRaises(DuplicateOfferError):
            create_offer(self.students[0], self.slots[0], self.slots[1])

    def test_old_offer_expired(self):
        old_offer = create_offer(self.students[0], self.slots[0], self.slots[1])

        old_offer.expires_at = now()
        old_offer.save()

        offer = create_offer(self.students[0], self.slots[0], self.slots[1])

        old_offer = Offer.objects.get(pk=old_offer.pk)

        self.assertEqual(old_offer.status, Offer.Status.EXPIRED)
        self.assertEqual(offer.status, Offer.Status.AWAITING)

    def test_expiry_is_capped_by_exam_deadline(self):
        deadline = now() + timedelta(hours=2)
        Exam.objects.filter(pk=self.exam.pk).update(slots_change_deadline=deadline)
        offered = Slot.objects.get(pk=self.slots[0].pk)
        wanted = Slot.objects.get(pk=self.slots[1].pk)

        offer = create_offer(self.students[0], offered, wanted)

        self.assertEqual(offer.expires_at, deadline)

    def test_raises_if_offered_slot_is_free(self):
        with self.assertRaises(NotSlotOwnerError):
            create_offer(self.students[0], self.slots[8], self.slots[1])


class OfferLimitTests(SchedulingTestCase):
    def make_extra_owned_slots(self, count):
        slots = []
        for i in range(count):
            user = User.objects.create_user(
                username=f'extra{i}', password='pass', role=User.Role.STUDENT
            )
            slot = self.slots[3 + i]
            slot.owner = user  # pyright: ignore[reportAttributeAccessIssue]
            slot.save()
            slots.append(slot)
        return slots

    def fill_limit(self):
        extra = self.make_extra_owned_slots(2)
        wanted = [self.slots[1], self.slots[2], extra[0]]
        offers = [
            create_offer(self.students[0], self.slots[0], slot) for slot in wanted
        ]
        return offers, extra[1]  # extra[1] нужен для четвёртой попытки

    def test_raises_when_limit_reached(self):
        _, fourth_wanted = self.fill_limit()

        with self.assertRaises(OfferLimitError):
            create_offer(self.students[0], self.slots[0], fourth_wanted)

        self.assertEqual(Offer.objects.count(), 3)

    def test_finished_offers_do_not_count(self):
        offers, fourth_wanted = self.fill_limit()
        Offer.objects.filter(pk=offers[0].pk).update(status=Offer.Status.CANCELED)

        offer = create_offer(self.students[0], self.slots[0], fourth_wanted)

        self.assertEqual(offer.status, Offer.Status.AWAITING)

    def test_expired_offers_do_not_count(self):
        offers, fourth_wanted = self.fill_limit()
        Offer.objects.filter(pk=offers[0].pk).update(
            expires_at=now() - timedelta(minutes=1)
        )

        offer = create_offer(self.students[0], self.slots[0], fourth_wanted)

        self.assertEqual(offer.status, Offer.Status.AWAITING)


class AcceptOfferTests(SchedulingTestCase):
    def test_swaps_slot_owners(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        accept_offer(offer.pk, s1)

        self.assertEqual(self.owner_of(self.slots[0]), s1)
        self.assertEqual(self.owner_of(self.slots[1]), s0)

    def test_marks_offer_accepted(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        accept_offer(offer.pk, s1)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.ACCEPTED)

    def test_writes_swap_log_snapshot(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        log = accept_offer(offer.pk, s1)

        self.assertEqual(SwapLog.objects.count(), 1)
        self.assertEqual(log.offer, offer)
        self.assertEqual(log.sender, s0)
        self.assertEqual(log.receiver, s1)
        self.assertEqual(log.sender_name, 'student0')
        self.assertEqual(log.receiver_name, 'student1')
        self.assertEqual(log.sender_slot_datetime, self.slots[0].start)
        self.assertEqual(log.receiver_slot_datetime, self.slots[1].start)
        self.assertEqual(log.exam_name, 'Test exam')

    def test_cancels_other_awaiting_offers_of_participants(self):
        s0, s1, _ = self.students
        main = create_offer(s0, self.slots[0], self.slots[1])

        other_from_offeror = create_offer(s0, self.slots[0], self.slots[2])

        other_from_acceptor = create_offer(s1, self.slots[1], self.slots[2])

        accept_offer(main.pk, s1)

        other_from_offeror.refresh_from_db()
        other_from_acceptor.refresh_from_db()
        self.assertEqual(other_from_offeror.status, Offer.Status.CANCELED)
        self.assertEqual(other_from_acceptor.status, Offer.Status.CANCELED)

    def test_keeps_offers_of_third_parties(self):
        s0, s1, s2 = self.students
        main = create_offer(s0, self.slots[0], self.slots[1])
        third_party = create_offer(s2, self.slots[2], self.slots[1])

        accept_offer(main.pk, s1)

        third_party.refresh_from_db()
        self.assertEqual(third_party.status, Offer.Status.AWAITING)

    def test_does_not_touch_finished_offers(self):
        s0, s1, _ = self.students
        old = create_offer(s0, self.slots[0], self.slots[1])
        Offer.objects.filter(pk=old.pk).update(status=Offer.Status.EXPIRED)
        new = create_offer(s0, self.slots[0], self.slots[1])

        accept_offer(new.pk, s1)

        old.refresh_from_db()
        self.assertEqual(old.status, Offer.Status.EXPIRED)

    def test_new_owner_can_accept_offer_for_swapped_slot(self):
        s0, s1, s2 = self.students
        first = create_offer(s0, self.slots[0], self.slots[1])

        second = create_offer(s2, self.slots[2], self.slots[1])

        accept_offer(first.pk, s1)

        with self.assertRaises(NotWantedSlotOwnerError):
            accept_offer(second.pk, s1)

        accept_offer(second.pk, s0)
        self.assertEqual(self.owner_of(self.slots[1]), s2)

    def test_raises_if_offer_already_accepted(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])
        accept_offer(offer.pk, s1)

        with self.assertRaises(OfferNotAwaitingError):
            accept_offer(offer.pk, s1)

    def test_raises_if_offer_expired(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])
        Offer.objects.filter(pk=offer.pk).update(
            expires_at=now() - timedelta(minutes=1)
        )

        with self.assertRaises(OfferExpiredError):
            accept_offer(offer.pk, s1)

    def test_raises_if_deadline_passed(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])
        Exam.objects.filter(pk=self.exam.pk).update(
            slots_change_deadline=now() - timedelta(minutes=1)
        )

        with self.assertRaises(DeadlinePassedError):
            accept_offer(offer.pk, s1)

    def test_raises_if_acceptor_does_not_own_wanted_slot(self):
        s0, _, s2 = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        with self.assertRaises(NotWantedSlotOwnerError):
            accept_offer(offer.pk, s2)

    def test_raises_if_offeror_lost_the_slot(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        Slot.objects.filter(pk=self.slots[0].pk).update(owner=None)

        with self.assertRaises(OfferOutdatedError):
            accept_offer(offer.pk, s1)


class ReleaseSlotTests(SchedulingTestCase):
    def test_frees_slot(self):
        release_slot(self.slots[0].pk, self.students[0])

        self.assertIsNone(self.owner_of(self.slots[0]))

    def test_raises_if_not_owner(self):
        with self.assertRaises(NotSlotOwnerError):
            release_slot(self.slots[0].pk, self.students[1])

    def test_raises_if_slot_is_already_free(self):
        with self.assertRaises(NotSlotOwnerError):
            release_slot(self.slots[8].pk, self.students[0])

    def test_raises_if_deadline_passed(self):
        Exam.objects.filter(pk=self.exam.pk).update(
            slots_change_deadline=now() - timedelta(minutes=1)
        )

        with self.assertRaises(DeadlinePassedError):
            release_slot(self.slots[0].pk, self.students[0])

        self.assertEqual(self.owner_of(self.slots[0]), self.students[0])

    def test_cancels_awaiting_offers_involving_slot(self):
        s0, s1, _ = self.students
        as_offered = create_offer(s0, self.slots[0], self.slots[1])
        as_wanted = create_offer(s1, self.slots[1], self.slots[0])
        unrelated = create_offer(s1, self.slots[1], self.slots[2])

        release_slot(self.slots[0].pk, s0)

        as_offered.refresh_from_db()
        as_wanted.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(as_offered.status, Offer.Status.CANCELED)
        self.assertEqual(as_wanted.status, Offer.Status.CANCELED)
        self.assertEqual(unrelated.status, Offer.Status.AWAITING)

    def test_keeps_finished_offers(self):
        s0 = self.students[0]
        expired = create_offer(s0, self.slots[0], self.slots[1])
        Offer.objects.filter(pk=expired.pk).update(status=Offer.Status.EXPIRED)
        accepted = create_offer(s0, self.slots[0], self.slots[1])
        Offer.objects.filter(pk=accepted.pk).update(status=Offer.Status.ACCEPTED)

        release_slot(self.slots[0].pk, s0)

        expired.refresh_from_db()
        accepted.refresh_from_db()
        self.assertEqual(expired.status, Offer.Status.EXPIRED)
        self.assertEqual(accepted.status, Offer.Status.ACCEPTED)


class TakeSlotTests(SchedulingTestCase):
    def test_takes_free_slot_when_user_has_none(self):
        newbie = User.objects.create_user(
            username='student3', password='pass', role=User.Role.STUDENT
        )
        Enrollment.objects.create(exam=self.exam, user=newbie)

        take_slot(self.slots[3].pk, newbie)

        self.assertEqual(self.owner_of(self.slots[3]), newbie)

    def test_moves_user_to_free_slot(self):
        s0 = self.students[0]

        take_slot(self.slots[3].pk, s0)

        self.assertEqual(self.owner_of(self.slots[3]), s0)
        self.assertIsNone(self.owner_of(self.slots[0]))

    def test_cancels_awaiting_offers_on_old_slot(self):
        s0, s1, _ = self.students
        as_offered = create_offer(s0, self.slots[0], self.slots[1])
        as_wanted = create_offer(s1, self.slots[1], self.slots[0])
        unrelated = create_offer(s1, self.slots[1], self.slots[2])

        take_slot(self.slots[3].pk, s0)

        as_offered.refresh_from_db()
        as_wanted.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(as_offered.status, Offer.Status.CANCELED)
        self.assertEqual(as_wanted.status, Offer.Status.CANCELED)
        self.assertEqual(unrelated.status, Offer.Status.AWAITING)

    def test_keeps_finished_offers(self):
        s0 = self.students[0]
        expired = create_offer(s0, self.slots[0], self.slots[1])
        Offer.objects.filter(pk=expired.pk).update(status=Offer.Status.EXPIRED)

        take_slot(self.slots[3].pk, s0)

        expired.refresh_from_db()
        self.assertEqual(expired.status, Offer.Status.EXPIRED)

    def test_raises_if_slot_is_taken(self):
        s0 = self.students[0]

        with self.assertRaises(SlotNotFreeError):
            take_slot(self.slots[1].pk, s0)

        self.assertEqual(self.owner_of(self.slots[0]), s0)

    def test_raises_if_slot_is_already_yours(self):
        with self.assertRaises(SlotAlreadyOwnedError):
            take_slot(self.slots[0].pk, self.students[0])

    def test_raises_if_deadline_passed(self):
        Exam.objects.filter(pk=self.exam.pk).update(
            slots_change_deadline=now() - timedelta(minutes=1)
        )

        with self.assertRaises(DeadlinePassedError):
            take_slot(self.slots[3].pk, self.students[0])

        self.assertEqual(self.owner_of(self.slots[0]), self.students[0])
        self.assertIsNone(self.owner_of(self.slots[3]))


class DeclineOfferTests(SchedulingTestCase):
    def test_recipient_declines_offer(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        decline_offer(offer.pk, s1)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.DECLINED)
        self.assertEqual(self.owner_of(self.slots[0]), s0)
        self.assertEqual(self.owner_of(self.slots[1]), s1)

    def test_raises_if_not_wanted_slot_owner(self):
        s0, _, s2 = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        with self.assertRaises(NotWantedSlotOwnerError):
            decline_offer(offer.pk, s2)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.AWAITING)

    def test_raises_if_offer_already_accepted(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])
        accept_offer(offer.pk, s1)

        with self.assertRaises(OfferNotAwaitingError):
            decline_offer(offer.pk, s0)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.ACCEPTED)

    def test_raises_if_offer_expired(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])
        Offer.objects.filter(pk=offer.pk).update(status=Offer.Status.EXPIRED)

        with self.assertRaises(OfferNotAwaitingError):
            decline_offer(offer.pk, s1)

    def test_new_slot_owner_can_decline(self):
        s0, s1, s2 = self.students
        first = create_offer(s0, self.slots[0], self.slots[1])
        second = create_offer(s2, self.slots[2], self.slots[1])
        accept_offer(first.pk, s1)  # слот 1 теперь у s0

        with self.assertRaises(NotWantedSlotOwnerError):
            decline_offer(second.pk, s1)

        decline_offer(second.pk, s0)
        second.refresh_from_db()
        self.assertEqual(second.status, Offer.Status.DECLINED)


class EnrollmentCheckTests(SchedulingTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.outsider = User.objects.create_user(
            username='outsider', password='pass', role=User.Role.STUDENT
        )

    def test_create_offer_requires_enrollment(self):
        with self.assertRaises(NotEnrolledError):
            create_offer(self.outsider, self.slots[0], self.slots[1])

        self.assertEqual(Offer.objects.count(), 0)

    def test_accept_offer_requires_enrollment(self):
        offer = create_offer(self.students[0], self.slots[0], self.slots[1])

        with self.assertRaises(NotEnrolledError):
            accept_offer(offer.pk, self.outsider)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.AWAITING)

    def test_decline_offer_requires_enrollment(self):
        offer = create_offer(self.students[0], self.slots[0], self.slots[1])

        with self.assertRaises(NotEnrolledError):
            decline_offer(offer.pk, self.outsider)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.AWAITING)

    def test_take_slot_requires_enrollment(self):
        with self.assertRaises(NotEnrolledError):
            take_slot(self.slots[3].pk, self.outsider)

        self.assertIsNone(self.owner_of(self.slots[3]))

    def test_release_slot_requires_enrollment(self):
        with self.assertRaises(NotEnrolledError):
            release_slot(self.slots[0].pk, self.outsider)

        self.assertEqual(self.owner_of(self.slots[0]), self.students[0])

    def test_removed_student_cannot_act(self):
        Enrollment.objects.filter(exam=self.exam, user=self.students[0]).delete()

        with self.assertRaises(NotEnrolledError):
            release_slot(self.slots[0].pk, self.students[0])


class AcceptInviteTests(SchedulingTestCase):
    def make_student(self, name):
        return User.objects.create_user(
            username=name, password='pass', role=User.Role.STUDENT
        )

    def make_invite(self, **kwargs):
        return Invite.objects.create(exam=self.exam, **kwargs)

    def test_enrolls_user_and_counts_use(self):
        invite = self.make_invite(usage_limit=5)
        user = self.make_student('newbie')

        enrollment = accept_invite(invite.token, user)

        self.assertEqual(enrollment.exam, self.exam)
        self.assertEqual(enrollment.user, user)
        invite.refresh_from_db()
        self.assertEqual(invite.usage_counter, 1)

    def test_repeated_use_does_not_burn_limit(self):
        invite = self.make_invite(usage_limit=1)
        user = self.make_student('newbie')
        first = accept_invite(invite.token, user)

        second = accept_invite(invite.token, user)

        self.assertEqual(first.pk, second.pk)
        invite.refresh_from_db()
        self.assertEqual(invite.usage_counter, 1)
        self.assertEqual(Enrollment.objects.filter(user=user).count(), 1)

    def test_enrolled_user_can_use_exhausted_revoked_link(self):
        invite = self.make_invite(usage_limit=1)
        user = self.make_student('newbie')
        accept_invite(invite.token, user)
        Invite.objects.filter(pk=invite.pk).update(is_revoked=True)

        enrollment = accept_invite(invite.token, user)

        self.assertEqual(enrollment.user, user)

    def test_unlimited_invite_works_for_many_users(self):
        invite = self.make_invite()

        for i in range(3):
            accept_invite(invite.token, self.make_student(f'user{i}'))

        invite.refresh_from_db()
        self.assertEqual(invite.usage_counter, 3)

    def test_raises_when_limit_reached(self):
        invite = self.make_invite(usage_limit=1)
        accept_invite(invite.token, self.make_student('first'))
        late = self.make_student('late')

        with self.assertRaises(InviteLimitReachedError):
            accept_invite(invite.token, late)

        self.assertFalse(Enrollment.objects.filter(user=late).exists())

    def test_raises_if_revoked(self):
        invite = self.make_invite(is_revoked=True)

        with self.assertRaises(InviteRevokedError):
            accept_invite(invite.token, self.make_student('newbie'))

    def test_raises_if_expired(self):
        invite = self.make_invite(expires_at=now() - timedelta(minutes=1))

        with self.assertRaises(InviteExpiredError):
            accept_invite(invite.token, self.make_student('newbie'))

    def test_raises_for_unknown_token(self):
        with self.assertRaises(InviteNotFoundError):
            accept_invite('no-such-token', self.make_student('newbie'))

    def test_teacher_cannot_enroll(self):
        invite = self.make_invite()

        with self.assertRaises(TeacherCannotEnrollError):
            accept_invite(invite.token, self.teacher)

        self.assertEqual(Enrollment.objects.filter(user=self.teacher).count(), 0)
        invite.refresh_from_db()
        self.assertEqual(invite.usage_counter, 0)


class JoinInviteViewTests(SchedulingTestCase):
    def setUp(self):
        self.invite = Invite.objects.create(exam=self.exam, usage_limit=5)
        self.url = reverse('join_invite', kwargs={'token': self.invite.token})
        self.exam_url = reverse('exam_detail', kwargs={'pk': self.exam.pk})
        self.newbie = User.objects.create_user(
            username='newbie', password='pass', role=User.Role.STUDENT
        )

    def test_unknown_token_returns_404(self):
        response = self.client.get(reverse('join_invite', kwargs={'token': 'nope'}))

        self.assertEqual(response.status_code, 404)

    def test_get_does_not_use_invite(self):
        self.client.force_login(self.newbie)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.usage_counter, 0)
        self.assertFalse(Enrollment.objects.filter(user=self.newbie).exists())

    def test_post_enrolls_and_redirects_to_exam(self):
        self.client.force_login(self.newbie)

        response = self.client.post(self.url)

        self.assertRedirects(response, self.exam_url, fetch_redirect_response=False)
        self.assertTrue(
            Enrollment.objects.filter(user=self.newbie, exam=self.exam).exists()
        )
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.usage_counter, 1)

    def test_already_enrolled_user_is_sent_to_exam(self):
        self.client.force_login(self.students[0])

        response = self.client.get(self.url)

        self.assertRedirects(response, self.exam_url, fetch_redirect_response=False)

    def test_enrolled_user_can_use_dead_link(self):
        Invite.objects.filter(pk=self.invite.pk).update(is_revoked=True)
        self.client.force_login(self.students[0])

        response = self.client.get(self.url)

        self.assertRedirects(response, self.exam_url, fetch_redirect_response=False)

    def test_anonymous_post_redirects_to_login(self):
        response = self.client.post(self.url)

        self.assertRedirects(
            response,
            f'{reverse("login")}?next={self.url}',
            fetch_redirect_response=False,
        )
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.usage_counter, 0)

    def test_dead_link_shows_problem_and_does_not_enroll(self):
        Invite.objects.filter(pk=self.invite.pk).update(is_revoked=True)
        self.client.force_login(self.newbie)

        get_response = self.client.get(self.url)
        post_response = self.client.post(self.url)

        for response in (get_response, post_response):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['problem'], str(InviteRevokedError()))
        self.assertFalse(Enrollment.objects.filter(user=self.newbie).exists())

    def test_teacher_sees_problem_and_does_not_enroll(self):
        self.client.force_login(self.teacher)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['problem'], str(TeacherCannotEnrollError()))
        self.assertFalse(Enrollment.objects.filter(user=self.teacher).exists())


class RegisterViewTests(SchedulingTestCase):
    def setUp(self):
        self.invite = Invite.objects.create(exam=self.exam, usage_limit=5)
        self.url = reverse('register', kwargs={'token': self.invite.token})
        self.join_url = reverse('join_invite', kwargs={'token': self.invite.token})
        self.exam_url = reverse('exam_detail', kwargs={'pk': self.exam.pk})
        self.data = {
            'username': 'newbie',
            'first_name': 'New',
            'last_name': 'Bie',
            'email': 'newbie@example.com',
            'password1': 'S3cure-pass-xyz',
            'password2': 'S3cure-pass-xyz',
        }

    def test_get_shows_form(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)

    def test_join_page_for_anonymous_links_to_register(self):
        response = self.client.get(self.join_url)

        self.assertContains(response, self.url)

    def test_post_creates_student_enrolls_and_logs_in(self):
        response = self.client.post(self.url, self.data)

        self.assertRedirects(response, self.exam_url, fetch_redirect_response=False)
        user = User.objects.get(username='newbie')
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertTrue(Enrollment.objects.filter(user=user, exam=self.exam).exists())
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.usage_counter, 1)

    def test_role_cannot_be_forged(self):
        self.client.post(self.url, {**self.data, 'role': User.Role.TEACHER})

        self.assertEqual(User.objects.get(username='newbie').role, User.Role.STUDENT)

    def test_invalid_form_shows_errors_and_creates_nothing(self):
        response = self.client.post(self.url, {**self.data, 'password2': 'different'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_dead_link_redirects_to_join_page(self):
        Invite.objects.filter(pk=self.invite.pk).update(is_revoked=True)

        response = self.client.post(self.url, self.data)

        self.assertRedirects(response, self.join_url, fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_logged_in_user_is_redirected(self):
        self.client.force_login(self.students[0])

        response = self.client.get(self.url)

        self.assertRedirects(response, self.join_url, fetch_redirect_response=False)

    def test_personal_invite_prefills_email(self):
        Invite.objects.filter(pk=self.invite.pk).update(email='named@example.com')

        response = self.client.get(self.url)

        self.assertEqual(response.context['form'].initial['email'], 'named@example.com')


class WithdrawOfferTests(SchedulingTestCase):
    def test_offeror_withdraws_offer(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        withdraw_offer(offer.pk, s0)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.WITHDRAWN)
        self.assertEqual(self.owner_of(self.slots[0]), s0)
        self.assertEqual(self.owner_of(self.slots[1]), s1)

    def test_raises_if_not_offeror(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])

        with self.assertRaises(NotOfferorError):
            withdraw_offer(offer.pk, s1)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.AWAITING)

    def test_raises_if_offer_already_accepted(self):
        s0, s1, _ = self.students
        offer = create_offer(s0, self.slots[0], self.slots[1])
        accept_offer(offer.pk, s1)

        with self.assertRaises(OfferNotAwaitingError):
            withdraw_offer(offer.pk, s0)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.ACCEPTED)

    def test_can_withdraw_after_deadline(self):
        s0 = self.students[0]
        offer = create_offer(s0, self.slots[0], self.slots[1])
        Exam.objects.filter(pk=self.exam.pk).update(
            slots_change_deadline=now() - timedelta(minutes=1)
        )

        withdraw_offer(offer.pk, s0)

        offer.refresh_from_db()
        self.assertEqual(offer.status, Offer.Status.WITHDRAWN)

    def test_same_offer_can_be_sent_again_after_withdrawal(self):
        s0 = self.students[0]
        offer = create_offer(s0, self.slots[0], self.slots[1])
        withdraw_offer(offer.pk, s0)

        again = create_offer(s0, self.slots[0], self.slots[1])

        self.assertEqual(again.status, Offer.Status.AWAITING)

    def test_requires_enrollment(self):
        s0 = self.students[0]
        offer = create_offer(s0, self.slots[0], self.slots[1])
        outsider = User.objects.create_user(
            username='outsider', password='pass', role=User.Role.STUDENT
        )

        with self.assertRaises(NotEnrolledError):
            withdraw_offer(offer.pk, outsider)


class HomeViewTests(SchedulingTestCase):
    def make_exam(self, name, days_from_now, enrolled=()):
        exam = Exam.objects.create(
            name=name,
            owner=self.teacher,
            slots_change_deadline=now() + timedelta(days=days_from_now + 7),
        )
        start = now() + timedelta(days=days_from_now)
        ExamDay.objects.create(exam=exam, start=start, end=start + timedelta(hours=1))
        for user in enrolled:
            Enrollment.objects.create(exam=exam, user=user)
        return exam

    def test_student_with_one_exam_is_redirected(self):
        self.client.force_login(self.students[0])

        response = self.client.get(reverse('home'))

        self.assertRedirects(
            response,
            reverse('exam_detail', kwargs={'pk': self.exam.pk}),
            fetch_redirect_response=False,
        )

    def test_student_with_two_exams_sees_list_by_date(self):
        later = self.make_exam('Later', 20, enrolled=[self.students[0]])
        self.client.force_login(self.students[0])

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['upcoming'], [self.exam, later])

    def test_finished_exam_goes_to_past(self):
        old = self.make_exam('Old', -5, enrolled=[self.students[0]])
        self.client.force_login(self.students[0])

        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['past'], [old])
        self.assertEqual(response.context['upcoming'], [self.exam])

    def test_teacher_sees_own_exams_without_redirect(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['upcoming'], [self.exam])

    def test_other_exams_are_hidden(self):
        self.make_exam('Not mine', 10, enrolled=[self.students[1]])
        self.make_exam('Mine too', 15, enrolled=[self.students[0]])
        self.client.force_login(self.students[0])

        response = self.client.get(reverse('home'))

        names = [e.name for e in response.context['upcoming']]
        self.assertEqual(names, ['Test exam', 'Mine too'])

    def test_user_without_exams_sees_hint(self):
        newbie = User.objects.create_user(
            username='newbie', password='pass', role=User.Role.STUDENT
        )
        self.client.force_login(newbie)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['upcoming'], [])
        self.assertEqual(response.context['past'], [])


class SaveExamTests(SchedulingTestCase):
    def setUp(self):
        self.base = now().replace(minute=0, second=0, microsecond=0) + timedelta(
            days=30
        )

    def call(self, **overrides):
        args = {
            'user': self.teacher,
            'exam': None,
            'name': 'New',
            'slot_duration': 20,
            'deadline': now() + timedelta(days=60),
            'days': [],
        }
        args.update(overrides)
        return save_exam(**args)

    def span(self, offset_hours=0, hours=1):
        start = self.base + timedelta(hours=offset_hours)
        return (start, start + timedelta(hours=hours))

    def test_creates_exam_with_days_and_slots(self):
        exam = self.call(days=[self.span(0, 1), self.span(24, 2)])

        self.assertEqual(exam.owner, self.teacher)
        self.assertEqual(exam.name, 'New')
        self.assertEqual(ExamDay.objects.filter(exam=exam).count(), 2)
        self.assertEqual(Slot.objects.filter(exam=exam).count(), 3 + 6)

    def test_failure_midway_rolls_back_everything(self):
        days_before = ExamDay.objects.count()
        slots_before = Slot.objects.count()

        with (
            patch(
                'scheduling.services.bulk_fill_slots',
                side_effect=[[], SlotGenerationError()],
            ),
            self.assertRaises(SlotGenerationError),
        ):
            self.call(days=[self.span(0, 1), self.span(24, 1)])

        self.assertFalse(Exam.objects.filter(name='New').exists())
        self.assertEqual(ExamDay.objects.count(), days_before)
        self.assertEqual(Slot.objects.count(), slots_before)

    def test_student_cannot_save_exam(self):
        with self.assertRaises(TeacherRequiredError):
            self.call(user=self.students[0])

        self.assertFalse(Exam.objects.filter(name='New').exists())

    def test_only_owner_can_update(self):
        other = User.objects.create_user(
            username='other', password='pass', role=User.Role.TEACHER
        )

        with self.assertRaises(NotExamOwnerError):
            self.call(user=other, exam=self.exam, name='Hijacked')

        self.exam.refresh_from_db()
        self.assertEqual(self.exam.name, 'Test exam')

    def test_update_changes_name_and_deadline_but_keeps_slots(self):
        new_deadline = now() + timedelta(days=3)

        self.call(exam=self.exam, name='Renamed', deadline=new_deadline)

        self.exam.refresh_from_db()
        self.assertEqual(self.exam.name, 'Renamed')
        self.assertEqual(self.exam.slots_change_deadline, new_deadline)
        self.assertEqual(Slot.objects.filter(exam=self.exam).count(), 9)

    def test_duration_is_locked_once_slots_exist(self):
        with self.assertRaises(SlotDurationLockedError):
            self.call(exam=self.exam, slot_duration=30)

        self.exam.refresh_from_db()
        self.assertEqual(self.exam.slot_duration, 20)

    def test_duration_can_change_before_any_slots_exist(self):
        exam = self.call()  # no days yet, so no slots

        self.call(exam=exam, slot_duration=30, days=[self.span(0, 1)])

        exam.refresh_from_db()
        self.assertEqual(exam.slot_duration, 30)
        self.assertEqual(Slot.objects.filter(exam=exam).count(), 2)

    def test_adds_day_to_existing_exam(self):
        self.call(exam=self.exam, days=[self.span(0, 1)])

        self.assertEqual(ExamDay.objects.filter(exam=self.exam).count(), 2)
        self.assertEqual(Slot.objects.filter(exam=self.exam).count(), 9 + 3)

    def test_overlap_with_existing_day_is_rejected(self):
        inside = (
            self.examday.start + timedelta(minutes=30),
            self.examday.start + timedelta(hours=1),
        )
        around = (
            self.examday.start - timedelta(hours=1),
            self.examday.end + timedelta(hours=1),
        )

        for days in ([inside], [around]):
            with self.assertRaises(OverlappingDaysError):
                self.call(exam=self.exam, days=days)

        self.assertEqual(ExamDay.objects.filter(exam=self.exam).count(), 1)

    def test_overlap_between_new_days_is_rejected(self):
        with self.assertRaises(OverlappingDaysError):
            self.call(days=[self.span(0, 2), self.span(1, 2)])

        self.assertFalse(Exam.objects.filter(name='New').exists())

    def test_touching_days_are_allowed(self):
        exam = self.call(days=[self.span(0, 1), self.span(1, 1)])

        self.assertEqual(Slot.objects.filter(exam=exam).count(), 6)

    def test_invalid_days_are_rejected(self):
        start = self.base

        with self.assertRaises(InvalidDayRangeError):
            self.call(days=[(start, start)])
        with self.assertRaises(DayShorterThanSlotError):
            self.call(days=[(start, start + timedelta(minutes=10))])

        self.assertFalse(Exam.objects.filter(name='New').exists())


class ExamEditViewTests(SchedulingTestCase):
    def setUp(self):
        self.new_url = reverse('exam_new')
        self.edit_url = reverse('exam_edit', kwargs={'pk': self.exam.pk})
        self.other_teacher = User.objects.create_user(
            username='other', password='pass', role=User.Role.TEACHER
        )
        self.future_date = localdate() + timedelta(days=40)

    def payload(self, days=(), **overrides):
        deadline = localtime(now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M')
        data = {
            'name': 'Brand new',
            'slot_duration': 20,
            'slots_change_deadline': deadline,
            'form-TOTAL_FORMS': len(days),
            'form-INITIAL_FORMS': 0,
            'form-MIN_NUM_FORMS': 0,
            'form-MAX_NUM_FORMS': 1000,
        }
        for i, (date, start, end) in enumerate(days):
            data[f'form-{i}-date'] = date.isoformat()
            data[f'form-{i}-start'] = start
            data[f'form-{i}-end'] = end
        data.update(overrides)
        return data

    # Access

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.new_url)

        self.assertRedirects(
            response,
            f'{reverse("login")}?next={self.new_url}',
            fetch_redirect_response=False,
        )

    def test_student_gets_403_on_new_and_edit(self):
        self.client.force_login(self.students[0])

        self.assertEqual(self.client.get(self.new_url).status_code, 403)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)

    def test_student_cannot_create_exam(self):
        self.client.force_login(self.students[0])

        response = self.client.post(self.new_url, self.payload())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Exam.objects.filter(name='Brand new').exists())

    def test_other_teacher_gets_404_on_foreign_exam(self):
        self.client.force_login(self.other_teacher)

        self.assertEqual(self.client.get(self.edit_url).status_code, 404)
        response = self.client.post(self.edit_url, self.payload(name='Hijacked'))

        self.assertEqual(response.status_code, 404)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.name, 'Test exam')

    # Showing the page

    def test_teacher_sees_empty_form_for_new_exam(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.new_url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['exam'])
        self.assertEqual(list(response.context['existing_days']), [])
        self.assertFalse(response.context['duration_locked'])

    def test_owner_sees_existing_exam(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.edit_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['name'], 'Test exam')
        self.assertEqual(list(response.context['existing_days']), [self.examday])
        self.assertTrue(response.context['duration_locked'])

    # Saving

    def test_post_creates_exam_with_days_and_slots(self):
        self.client.force_login(self.teacher)
        days = [
            (self.future_date, '09:00', '10:00'),  # 3 slots
            (self.future_date + timedelta(days=1), '14:00', '15:40'),  # 5 slots
        ]

        response = self.client.post(self.new_url, self.payload(days=days))

        exam = Exam.objects.get(name='Brand new')
        self.assertRedirects(
            response,
            reverse('exam_edit', kwargs={'pk': exam.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(exam.owner, self.teacher)
        self.assertEqual(ExamDay.objects.filter(exam=exam).count(), 2)
        self.assertEqual(Slot.objects.filter(exam=exam).count(), 8)

    def test_blank_and_deleted_rows_are_ignored(self):
        self.client.force_login(self.teacher)
        data = self.payload(
            days=[
                (self.future_date, '09:00', '10:00'),
                (self.future_date, '', ''),
            ]
        )
        data['form-0-DELETE'] = 'on'
        data['form-1-date'] = ''

        response = self.client.post(self.new_url, data)

        exam = Exam.objects.get(name='Brand new')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExamDay.objects.filter(exam=exam).count(), 0)

    def test_invalid_form_creates_nothing_and_keeps_input(self):
        self.client.force_login(self.teacher)
        days = [(self.future_date, '09:00', '10:00')]

        response = self.client.post(self.new_url, self.payload(days=days, name=''))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        formset = response.context['day_formset']
        self.assertEqual(formset.forms[0]['start'].value(), '09:00')
        self.assertFalse(Exam.objects.filter(name='').exists())
        self.assertEqual(ExamDay.objects.count(), 1)  # only the setUp day

    def test_invalid_day_is_reported_without_saving(self):
        self.client.force_login(self.teacher)
        days = [(self.future_date, '10:00', '09:00')]  # ends before it starts

        response = self.client.post(self.new_url, self.payload(days=days))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['day_formset'].errors[0])
        self.assertFalse(Exam.objects.filter(name='Brand new').exists())

    def test_service_error_is_shown_in_form_and_nothing_is_saved(self):
        self.client.force_login(self.teacher)
        days = [
            (self.future_date, '09:00', '10:00'),
            (self.future_date, '09:30', '10:30'),  # overlaps the first one
        ]

        response = self.client.post(self.new_url, self.payload(days=days))

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            str(OverlappingDaysError()),
            list(response.context['form'].non_field_errors()),
        )
        self.assertFalse(Exam.objects.filter(name='Brand new').exists())

    def test_edit_adds_day_and_keeps_existing_slots(self):
        self.client.force_login(self.teacher)
        days = [(self.future_date, '09:00', '10:00')]

        response = self.client.post(
            self.edit_url, self.payload(days=days, name='Test exam')
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExamDay.objects.filter(exam=self.exam).count(), 2)
        self.assertEqual(Slot.objects.filter(exam=self.exam).count(), 9 + 3)

    def test_locked_duration_is_ignored_but_name_is_saved(self):
        self.client.force_login(self.teacher)

        response = self.client.post(
            self.edit_url, self.payload(name='Renamed', slot_duration=30)
        )

        self.assertEqual(response.status_code, 302)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.name, 'Renamed')
        self.assertEqual(self.exam.slot_duration, 20)


class ExamDetailRoleTests(SchedulingTestCase):
    def setUp(self):
        self.url = reverse('exam_detail', kwargs={'pk': self.exam.pk})
        self.edit_url = reverse('exam_edit', kwargs={'pk': self.exam.pk})
        self.take_url = reverse('slot_take', kwargs={'slot_id': self.slots[3].pk})

    def test_owner_sees_edit_link_names_and_no_student_actions(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_owner'])
        self.assertContains(response, self.edit_url)
        self.assertContains(response, 'student0')
        self.assertNotContains(response, self.take_url)

    def test_student_sees_actions_but_no_edit_link(self):
        self.client.force_login(self.students[0])

        response = self.client.get(self.url)

        self.assertFalse(response.context['is_owner'])
        self.assertContains(response, self.take_url)
        self.assertNotContains(response, self.edit_url)

    def test_other_teacher_gets_404(self):
        other = User.objects.create_user(
            username='other', password='pass', role=User.Role.TEACHER
        )
        self.client.force_login(other)

        self.assertEqual(self.client.get(self.url).status_code, 404)


class RoleRulesTests(SchedulingTestCase):
    def make_student(self, name='extra'):
        return User.objects.create_user(
            username=name, password='pass', role=User.Role.STUDENT
        )

    def test_teacher_cannot_be_enrolled(self):
        enrollment = Enrollment(exam=self.exam, user=self.teacher)

        with self.assertRaises(ValidationError):
            enrollment.full_clean()

    def test_student_can_be_enrolled(self):
        enrollment = Enrollment(exam=self.exam, user=self.make_student())

        enrollment.full_clean()  # does not raise

    def test_student_cannot_own_exam(self):
        exam = Exam(
            name='X',
            owner=self.students[0],
            slot_duration=20,
            slots_change_deadline=now() + timedelta(days=1),
        )

        with self.assertRaises(ValidationError):
            exam.full_clean()

    def test_teacher_can_own_exam(self):
        exam = Exam(
            name='X',
            owner=self.teacher,
            slot_duration=20,
            slots_change_deadline=now() + timedelta(days=1),
        )

        exam.full_clean()  # does not raise

    def test_enrollment_form_offers_only_students(self):
        form = modelform_factory(Enrollment, fields=('exam', 'user'))()

        users = form.fields['user'].queryset  # pyright: ignore[reportAttributeAccessIssue]

        self.assertIn(self.students[0], users)
        self.assertNotIn(self.teacher, users)

    def test_exam_form_offers_only_teachers(self):
        form = modelform_factory(Exam, fields=('owner',))()

        owners = form.fields['owner'].queryset  # pyright: ignore[reportAttributeAccessIssue]

        self.assertIn(self.teacher, owners)
        self.assertNotIn(self.students[0], owners)

    def test_teacher_cannot_own_slot(self):
        slot = Slot(
            exam=self.exam, exam_day=self.examday, start=now(), owner=self.teacher
        )

        with self.assertRaises(ValidationError):
            slot.clean()

    def test_student_and_nobody_can_own_slot(self):
        Slot(
            exam=self.exam, exam_day=self.examday, start=now(), owner=self.students[0]
        ).clean()
        Slot(exam=self.exam, exam_day=self.examday, start=now()).clean()

    def test_teacher_cannot_be_offeror(self):
        offer = Offer(
            offeror=self.teacher, offered_slot=self.slots[0], wanted_slot=self.slots[1]
        )

        with self.assertRaises(ValidationError):
            offer.clean()

    def test_student_can_be_offeror(self):
        Offer(
            offeror=self.students[0],
            offered_slot=self.slots[0],
            wanted_slot=self.slots[1],
        ).clean()

    def test_exam_form_without_owner_is_valid(self):
        form = ExamForm(
            {
                'name': 'X',
                'slot_duration': 20,
                'slots_change_deadline': '2030-01-01 10:00',
            }
        )

        self.assertTrue(form.is_valid())


class DeleteExamDayTests(SchedulingTestCase):
    def make_empty_day(self):
        start = now() + timedelta(days=30)
        day = ExamDay.objects.create(
            exam=self.exam, start=start, end=start + timedelta(hours=1)
        )
        bulk_fill_slots(day)
        return day

    def test_deletes_day_and_its_slots_only(self):
        day = self.make_empty_day()

        delete_exam_day(day.pk, self.teacher)

        self.assertFalse(ExamDay.objects.filter(pk=day.pk).exists())
        self.assertFalse(Slot.objects.filter(exam_day_id=day.pk).exists())
        self.assertEqual(Slot.objects.filter(exam_day=self.examday).count(), 9)

    def test_raises_if_any_slot_is_owned(self):
        with self.assertRaises(DayHasOwnedSlotsError):
            delete_exam_day(self.examday.pk, self.teacher)

        self.assertTrue(ExamDay.objects.filter(pk=self.examday.pk).exists())
        self.assertEqual(Slot.objects.filter(exam_day=self.examday).count(), 9)

    def test_released_slots_do_not_block_deletion(self):
        for slot, student in zip(self.slots[:3], self.students):
            release_slot(slot.pk, student)

        delete_exam_day(self.examday.pk, self.teacher)

        self.assertFalse(ExamDay.objects.filter(pk=self.examday.pk).exists())

    def test_only_exam_owner_can_delete(self):
        day = self.make_empty_day()
        other = User.objects.create_user(
            username='other', password='pass', role=User.Role.TEACHER
        )

        for user in (other, self.students[0]):
            with self.assertRaises(NotExamOwnerError):
                delete_exam_day(day.pk, user)

        self.assertTrue(ExamDay.objects.filter(pk=day.pk).exists())

    def test_unknown_day_raises_does_not_exist(self):
        with self.assertRaises(ExamDay.DoesNotExist):
            delete_exam_day(999999, self.teacher)

    def test_deleting_all_days_unlocks_slot_duration(self):
        for slot, student in zip(self.slots[:3], self.students):
            release_slot(slot.pk, student)
        delete_exam_day(self.examday.pk, self.teacher)

        save_exam(
            self.teacher,
            self.exam,
            'Test exam',
            30,
            self.exam.slots_change_deadline,
            [],
        )

        self.exam.refresh_from_db()
        self.assertEqual(self.exam.slot_duration, 30)


class ExamDayDeleteViewTests(SchedulingTestCase):
    def setUp(self):
        start = now() + timedelta(days=30)
        self.empty_day = ExamDay.objects.create(
            exam=self.exam, start=start, end=start + timedelta(hours=1)
        )
        bulk_fill_slots(self.empty_day)
        self.url = reverse('examday_delete', kwargs={'pk': self.empty_day.pk})
        self.edit_url = reverse('exam_edit', kwargs={'pk': self.exam.pk})

    def test_owner_deletes_empty_day_and_returns_to_editor(self):
        self.client.force_login(self.teacher)

        response = self.client.post(self.url)

        self.assertRedirects(response, self.edit_url, fetch_redirect_response=False)
        self.assertFalse(ExamDay.objects.filter(pk=self.empty_day.pk).exists())

    def test_day_with_booked_slots_is_kept_with_message(self):
        self.client.force_login(self.teacher)
        url = reverse('examday_delete', kwargs={'pk': self.examday.pk})

        response = self.client.post(url, follow=True)

        self.assertTrue(ExamDay.objects.filter(pk=self.examday.pk).exists())
        self.assertContains(response, str(DayHasOwnedSlotsError()))

    def test_get_is_not_allowed(self):
        self.client.force_login(self.teacher)

        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ExamDay.objects.filter(pk=self.empty_day.pk).exists())

    def test_other_teacher_gets_404_and_day_survives(self):
        other = User.objects.create_user(
            username='other', password='pass', role=User.Role.TEACHER
        )
        self.client.force_login(other)

        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertTrue(ExamDay.objects.filter(pk=self.empty_day.pk).exists())

    def test_student_gets_404(self):
        self.client.force_login(self.students[0])

        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertTrue(ExamDay.objects.filter(pk=self.empty_day.pk).exists())

    def test_editor_hides_delete_button_for_booked_day(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.edit_url)

        self.assertContains(response, self.url)
        self.assertNotContains(
            response, reverse('examday_delete', kwargs={'pk': self.examday.pk})
        )
