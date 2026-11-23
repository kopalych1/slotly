from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from accounts.models import User


class ProfileViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse('profile')
        cls.user = User.objects.create_user(
            username='anna',
            password='pass',
            first_name='Anna',
            last_name='Peeters',
            email='anna@example.com',
            role=User.Role.STUDENT,
        )
        cls.other = User.objects.create_user(
            username='ben',
            password='pass',
            first_name='Ben',
            last_name='Janssens',
            email='ben@example.com',
            role=User.Role.TEACHER,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def data(self, **overrides):
        data = {
            'username': 'anna',
            'first_name': 'Anna',
            'last_name': 'Peeters',
            'email': 'anna@example.com',
        }
        data.update(overrides)
        return data

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f'{reverse("login")}?next={self.url}')

    def test_get_shows_current_values(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'anna@example.com')
        self.assertContains(response, 'Peeters')

    def test_valid_post_saves_and_redirects(self):
        response = self.client.post(
            self.url,
            self.data(first_name='Anne', last_name='Peeters-Maes'),
        )
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Anne')
        self.assertEqual(self.user.last_name, 'Peeters-Maes')
        levels = [m.level_tag for m in get_messages(response.wsgi_request)]
        self.assertEqual(levels, ['success'])

    def test_email_is_lowercased(self):
        self.client.post(self.url, self.data(email='Anna.New@Example.COM'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'anna.new@example.com')

    def test_can_change_username_and_stay_logged_in(self):
        response = self.client.post(self.url, self.data(username='anna2'))
        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'anna2')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_email_used_by_someone_else_is_rejected(self):
        response = self.client.post(self.url, self.data(email='BEN@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['form'].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'anna@example.com')

    def test_username_used_by_someone_else_is_rejected_ignoring_case(self):
        response = self.client.post(self.url, self.data(username='Ben'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('username', response.context['form'].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'anna')

    def test_names_and_email_are_required(self):
        for field in ('first_name', 'last_name', 'email'):
            with self.subTest(field=field):
                response = self.client.post(self.url, self.data(**{field: ''}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)

    def test_role_cannot_be_changed_through_the_form(self):
        self.client.post(self.url, self.data(role=User.Role.TEACHER))
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.STUDENT)

    def test_header_links_to_profile(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, f'href="{self.url}"')


def test_invalid_post_does_not_leak_into_header(self):
    response = self.client.post(
        self.url, self.data(first_name='Changed', email='ben@example.com')
    )
    self.assertEqual(response.context['user'].first_name, 'Anna')


class PasswordChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse('password_change')
        cls.user = User.objects.create_user(
            username='anna',
            password='old-Pass-123',
            first_name='Anna',
            last_name='Peeters',
            email='anna@example.com',
            role=User.Role.STUDENT,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def data(self, **overrides):
        data = {
            'old_password': 'old-Pass-123',
            'new_password1': 'new-Strong-456',
            'new_password2': 'new-Strong-456',
        }
        data.update(overrides)
        return data

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f'{reverse("login")}?next={self.url}')

    def test_get_shows_form(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_success_changes_password_and_redirects_to_profile(self):
        response = self.client.post(self.url, self.data())
        self.assertRedirects(response, reverse('profile'))
        levels = [m.level_tag for m in get_messages(response.wsgi_request)]
        self.assertEqual(levels, ['success'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('new-Strong-456'))

    def test_stays_logged_in_after_change(self):
        self.client.post(self.url, self.data())
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)

    def test_wrong_old_password_is_rejected(self):
        response = self.client.post(self.url, self.data(old_password='nope'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('old_password', response.context['form'].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-Pass-123'))

    def test_mismatching_new_passwords_are_rejected(self):
        response = self.client.post(self.url, self.data(new_password2='different-789'))
        self.assertIn('new_password2', response.context['form'].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-Pass-123'))

    def test_weak_password_is_rejected(self):
        for weak in ('short', 'password'):
            with self.subTest(weak=weak):
                response = self.client.post(
                    self.url, self.data(new_password1=weak, new_password2=weak)
                )
                self.assertIn('new_password2', response.context['form'].errors)


class ProfileRoleTests(TestCase):
    def test_profile_shows_role(self):
        for role, label in (
            (User.Role.STUDENT, 'Student'),
            (User.Role.TEACHER, 'Teacher'),
        ):
            with self.subTest(role=role):
                user = User.objects.create_user(
                    username=f'u-{role}', password='pass', role=role
                )
                self.client.force_login(user)
                self.assertContains(self.client.get(reverse('profile')), label)
