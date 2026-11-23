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
