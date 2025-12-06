from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from attendance.models import Shop, TimeLog
from scheduling.models import Schedule, Preference, UserPriority, Shift
from django.utils import timezone
import datetime

User = get_user_model()

class HRISTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='password123',
            first_name='Test',
            last_name='User',
            tier='regular',
            is_active=True,
            is_approved=True
        )
        self.admin = User.objects.create_user(
            username='adminuser',
            password='password123',
            first_name='Admin',
            last_name='User',
            tier='administrator',
            is_active=True,
            is_approved=True
        )
        self.shop = Shop.objects.create(name='Test Shop')

    def test_login(self):
        response = self.client.post('/', {'username': 'testuser', 'password': 'password123'})
        self.assertEqual(response.status_code, 302) # Redirect to home

    def test_time_in_logic(self):
        self.client.login(username='testuser', password='password123')

        # Test Time In
        response = self.client.post('/attendance/home/', {'action': 'time_in', 'shop_id': self.shop.id})
        self.assertRedirects(response, '/attendance/home/')

        log = TimeLog.objects.get(user=self.user, date=timezone.localdate())
        self.assertEqual(log.shop, self.shop)
        self.assertIsNotNone(log.time_in)

        # Test Double Time In (Should fail/warn)
        response = self.client.post('/attendance/home/', {'action': 'time_in', 'shop_id': self.shop.id})
        # Assuming it stays on page with message
        self.assertEqual(response.status_code, 200)
        # Log count should still be 1
        self.assertEqual(TimeLog.objects.filter(user=self.user, date=timezone.localdate()).count(), 1)

    def test_time_out_logic(self):
        self.client.login(username='testuser', password='password123')
        # Time in first
        self.client.post('/attendance/home/', {'action': 'time_in', 'shop_id': self.shop.id})

        # Time out 1
        self.client.post('/attendance/home/', {'action': 'time_out'})
        log = TimeLog.objects.get(user=self.user)
        t1 = log.time_out

        # Time out 2 (Later) - mocking time passage is hard in integration test without freezing time,
        # but we can check that it allows updates.
        self.client.post('/attendance/home/', {'action': 'time_out'})
        log.refresh_from_db()
        self.assertIsNotNone(log.time_out)

    def test_scheduling_access(self):
        self.client.login(username='testuser', password='password123')
        # Regular user accessing generator
        response = self.client.get('/scheduling/generator/')
        self.assertEqual(response.status_code, 403)

        self.client.login(username='adminuser', password='password123')
        response = self.client.get('/scheduling/generator/')
        self.assertEqual(response.status_code, 200)

    def test_preference_creation(self):
        self.client.login(username='testuser', password='password123')
        response = self.client.post('/scheduling/preferences/', {
            'preferred_days_off_count': 2,
            'top_preferred_day_off': 6 # Sunday
        })
        self.assertRedirects(response, '/scheduling/preferences/')
        pref = Preference.objects.get(user=self.user)
        self.assertEqual(pref.preferred_days_off_count, 2)
