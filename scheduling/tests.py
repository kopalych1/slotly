from datetime import timedelta

from django.test import TestCase
from django.utils.timezone import now

from accounts.models import User
from scheduling.exceptions import (
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
    NotSlotOwnerError,
    NotWantedSlotOwnerError,
    OfferExpiredError,
    OfferLimitError,
    OfferNotAwaitingError,
    OfferOutdatedError,
    SameSlotError,
    SlotAlreadyOwnedError,
    SlotNotFreeError,
    SlotsAlreadyExistError,
    TeacherCannotEnrollError,
    WantedSlotFreeError,
)
from scheduling.models import Enrollment, Exam, ExamDay, Invite, Offer, Slot, SwapLog
from scheduling.services import (
    accept_invite,
    accept_offer,
    bulk_fill_slots,
    create_offer,
    decline_offer,
    release_slot,
    take_slot,
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
        self.assertEqual(offer.status, Offer.Status.CANCELED)
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
        self.assertEqual(second.status, Offer.Status.CANCELED)


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
